"""Versioned exact metadata calculations; native v19 is preserved, not patched.

AST3 uses conditional_count/child_sum/parent_match_count/group_count in addition
to reusable primitive operators. Source/body admission precedes CSV access.
"""
from collections import Counter,defaultdict
from decimal import Decimal,localcontext
from copy import deepcopy
from datetime import datetime
import math
from common_io import ROOT,digest,read,sha
import generalized_measurement_v19 as native
from generalized_content_policy_v16 import assert_profile_admission
from generalized_profile_v16 import read_dataset
from generalized_runtime_context_v21 import source_scope

VERSION='metadata-calculation-3.0.4'
OPS={'column':0,'literal':0,'is_missing':1,'subtract':2,'divide':2,'eq':2,'lt':2,'lte':2,'gt':2,'gte':2,'and':2,
     'datetime_diff':2,'convert_unit':1,'child_count':0,'conditional_count':0,'child_sum':0,'parent_match_count':0,'group_count':0}
NUMERIC={'integer','number'}

class PlanError(ValueError):
    def __init__(self,code,path,message):self.code,self.path=code,path;super().__init__(message)

class Engine:
    def __init__(self,inventory,policy,capture=None,max_rows=10000):
        self.inventory,self.policy,self.capture=inventory,policy,capture or {}
        self.tables={t['name']:t for t in inventory['tables']}
        self.columns={(t['name'],c['name']):c for t in inventory['tables'] for c in t['columns']}
        self.relations={r['id']:r for r in inventory.get('relationships',[])}
        # Existing source policy also verifies transitive held-source exclusions.
        with source_scope(inventory,policy,relationship_capture_scope=self.capture):
            assert_profile_admission(inventory)
            self.rows=read_dataset(inventory,project_root=ROOT,max_rows=max_rows) if any(t.get('csv_source') for t in inventory['tables']) else {t['name']:[] for t in inventory['tables']}
        self.child_index={};self.parent_index={};self.group_cache={}
        for rid,r in self.relations.items():
            children=defaultdict(list);parents=defaultdict(list)
            for row in self.rows.get(r['from_table'],[]):
                key=self.key(row,r['from_table'],r['from_columns'])
                if key is not None:children[key].append(row)
            for row in self.rows.get(r['to_table'],[]):
                key=self.key(row,r['to_table'],r['to_columns'])
                if key is not None:parents[key].append(row)
            self.child_index[rid],self.parent_index[rid]=children,parents

    def missing(self,raw,table,column):
        return raw is None or raw in self.columns[(table,column)].get('missing_tokens',[''])
    def key(self,row,table,keys):
        values=tuple(row.get(k) for k in keys)
        return None if any(self.missing(v,table,k) for v,k in zip(values,keys)) else values
    def column(self,row,table,column):
        info=self.columns[(table,column)];raw=row.get(column)
        if self.missing(raw,table,column):return None
        typ=native._type(info)
        if typ in NUMERIC:
            value=native._number(raw)
            return None if typ=='integer' and value is not None and value!=value.to_integral_value() else value
        if typ=='timestamp':return native._timestamp(raw)
        if typ=='boolean':return raw if type(raw)is bool else {'true':True,'false':False,'1':True,'0':False}.get(str(raw).lower())
        return str(raw)

    def infer(self,node,table,path='/expression',depth=0):
        if depth>16:raise PlanError('AST_depth',path,'Maximum depth16')
        if not isinstance(node,dict) or node.get('op') not in OPS:raise PlanError('unsupported_op',path,'Operator is not supported by AST3')
        op=node['op'];self.nodes+=1
        if self.nodes>128:raise PlanError('AST_nodes',path,'Maximum128 nodes')
        base={'op','args'} if OPS[op] else {'op'}
        extra={'column':{'table','column'},'literal':{'value'},'datetime_diff':{'unit'},'convert_unit':{'from_unit','to_unit'},'child_count':{'relationship_id'},
               'conditional_count':{'relationship_id','where'},'child_sum':{'relationship_id','where','value'},
               'parent_match_count':{'relationship_id'},'group_count':{'keys','where'}}.get(op,set())
        if set(node)!=base|extra:raise PlanError('AST_fields',path,'Required exact fields: '+str(sorted(base|extra)))
        args=node.get('args',[])
        if OPS[op] and (not isinstance(args,list) or len(args)!=OPS[op]):raise PlanError('AST_arity',path+'/args','Wrong argument count')
        if op=='column':
            k=(node['table'],node['column'])
            if node['table']!=table or k not in self.columns:raise PlanError('column_scope',path,'Use an admitted column from the current analysis table')
            c=self.columns[k]
            if c.get('excluded') or c.get('scope_status')=='excluded':raise PlanError('body_column',path,'Excluded content is forbidden')
            if self.inventory.get('scope_admission',{}).get('admitted') is False:raise PlanError('scope',path,'Profile not admitted')
            self.sources.add(k)
            return native._type(c),(c.get('unit')or{}).get('name') if (c.get('unit')or{}).get('status')=='verified' else None
        if op=='literal':
            v=node['value']
            if type(v)not in {str,bool,int,float,type(None)} or isinstance(v,float) and not math.isfinite(v):raise PlanError('literal_type',path,'Finite scalar required')
            return ('boolean','boolean') if type(v)is bool else ('number',None) if type(v)in {int,float} else ('category',None)
        if op in {'child_count','conditional_count','child_sum','parent_match_count'}:
            rid=node['relationship_id'];r=self.relations.get(rid)
            if r is None:raise PlanError('relationship',path,'Unknown relationship')
            expected=r['from_table'] if op=='parent_match_count' else r['to_table']
            if table!=expected:raise PlanError('relationship_grain',path,'Wrong relationship analysis table')
            self.used_relations.add(rid)
            for t,cs in [(r['from_table'],r['from_columns']),(r['to_table'],r['to_columns'])]:
                for c in cs:
                    if (t,c)not in self.columns:raise PlanError('relationship_column',path,'Relationship key column is absent')
                    if self.columns[(t,c)].get('excluded'):raise PlanError('body_relationship_key',path,'Excluded content cannot be a relationship key')
                    if native._type(self.columns[(t,c)])!='category':raise PlanError('unsupported_relationship_key_normalization',path,'Relation operator is supported for category keys; numeric/Boolean/timestamp key normalization is not implemented and must be held')
                    self.sources.add((t,c))
            if op!='parent_match_count':
                self.aggregates=True;self.aggregate_relations.add(rid)
                # Capture is required for absence/counts; an incomplete capture
                # is not silently interpreted as zero or a complete business log.
                proof=self.capture.get(rid)
                if isinstance(proof,dict) and 'source' in proof and 'value' in proof:
                    source=proof['source'];capture_path=(ROOT/source['path']).resolve();capture_path.relative_to(ROOT)
                    if sha(capture_path)!=source['sha256']:raise PlanError('capture_source',path,'Capture source SHA changed')
                    current=read(capture_path)
                    for token in source['pointer'].strip('/').split('/'):
                        token=token.replace('~1','/').replace('~0','~')
                        current=current[int(token)] if isinstance(current,list) else current[token]
                    if current!=proof['value']:raise PlanError('capture_pointer','/expression','Capture facts differ from the exact source pointer')
                    proof=proof['value']
                if proof is None:
                    entries=self.capture.get('relationship_coverage',self.capture.get('relationships',[]))
                    proof=next((x for x in entries if x.get('relationship_id',x.get('id'))==rid),None) if isinstance(entries,list) else entries.get(rid)
                definition={k:r.get(k,[]) if k=='no_reference_values' else r.get(k) for k in ('id','from_table','from_columns','to_table','to_columns','no_reference_values')}
                if (proof is None or proof.get('status')!='complete_for_selected_parents'
                        or proof.get('child_count_allowed')is not True or proof.get('schema_version')!=self.inventory['schema_version']
                        or proof.get('relationship_definition')!=definition
                        or proof.get('relationship_definition_sha')!=digest(definition)
                        or proof.get('parent_source_sha256')!=(self.tables[r['to_table']].get('csv_source')or{}).get('sha256')
                        or proof.get('child_source_sha256')!=(self.tables[r['from_table']].get('csv_source')or{}).get('sha256')):self.capture_missing.add(rid)
                if op in {'conditional_count','child_sum'} and node['where'] is not None:
                    kind,_=self.infer(node['where'],r['from_table'],path+'/where',depth+1)
                    if kind!='boolean':raise PlanError('filter_type',path+'/where','Child predicate must be Boolean')
                if op=='child_sum':
                    kind,unit=self.infer(node['value'],r['from_table'],path+'/value',depth+1)
                    if kind not in NUMERIC:raise PlanError('sum_type',path+'/value','Only numerical child values may be summed')
                    return 'number',unit
            return 'integer','count'
        if op=='group_count':
            self.aggregates=True
            if not isinstance(node['keys'],list) or not node['keys'] or len(set(node['keys']))!=len(node['keys']):raise PlanError('group_keys',path+'/keys','Nonempty unique categorical grouping columns required')
            for k in node['keys']:self.infer({'op':'column','table':table,'column':k},table,path+'/keys',depth+1)
            if node['where'] is not None and self.infer(node['where'],table,path+'/where',depth+1)[0]!='boolean':raise PlanError('filter_type',path+'/where','Group predicate must be Boolean')
            return 'integer','count'
        typed=[self.infer(a,table,path+'/args/'+str(i),depth+1) for i,a in enumerate(args)]
        if op=='is_missing':return 'boolean','boolean'
        if op=='and':
            if any(t!='boolean' for t,u in typed):raise PlanError('logical_type',path,'Boolean operands required')
            return 'boolean','boolean'
        if op in {'eq','lt','lte','gt','gte'}:
            if all(u for t,u in typed) and typed[0][1]!=typed[1][1]:raise PlanError('comparison_unit_mismatch',path,'Explicit unit conversion required before comparing verified physical units')
            if op=='eq' and not (typed[0][0]==typed[1][0] or all(t in NUMERIC for t,u in typed)):
                raise PlanError('comparison_type',path,'Equality operands must have compatible physical types')
            if op!='eq' and not (all(t in NUMERIC for t,u in typed) or all(t=='timestamp' for t,u in typed)):raise PlanError('comparison_type',path,'Ordered comparison needs compatible numeric/timestamp operands')
            return 'boolean','boolean'
        if op=='datetime_diff':
            if any(t!='timestamp' for t,u in typed) or node['unit']not in {'ms','s','us'}:raise PlanError('datetime_structure',path,'Two datetime arguments and supported output unit required')
            return 'number',node['unit']
        if op=='convert_unit':
            if typed[0][0]not in NUMERIC or typed[0][1]!=node['from_unit'] or node['from_unit']not in {'ms','s','us'} or node['to_unit']not in {'ms','s','us'}:
                raise PlanError('unit_conversion_evidence',path,'Verified input unit and supported explicit time-unit conversion required')
            return 'number',node['to_unit']
        if any(t not in NUMERIC for t,u in typed):raise PlanError('numeric_type',path,'Numeric operands required')
        units=[u for t,u in typed]
        if all(units) and units[0]!=units[1]:raise PlanError('unit_mismatch',path,'Explicit conversion required before arithmetic')
        if op=='divide':self.division=True;return 'number','ratio' if all(units) else None
        return 'number',units[0] if all(units) else None

    def validate(self,plan):
        self.nodes=0;self.sources=set();self.used_relations=set();self.capture_missing=set();self.aggregates=False;self.division=False;self.aggregate_relations=set()
        try:
            required={'id','expression','grain','population','group_by','missing_policy','schema_version','source_version'}
            if not required<=set(plan):raise PlanError('plan_fields','/','Required plan fields missing')
            if set(plan)-required-{'description','origin','plan_version'}:raise PlanError('plan_fields','/','Type/unit/version context must be code attached, not resubmitted')
            if plan['schema_version']!=self.inventory['schema_version'] or plan['source_version']!=self.inventory['source_version']:raise PlanError('source_version','/source_version','Stale schema/source version')
            def contains_group(node):
                return isinstance(node,dict) and (node.get('op')=='group_count' or any(contains_group(a) for a in node.get('args',[])) or contains_group(node.get('where')) or contains_group(node.get('value')))
            if contains_group(plan['population']['filter']):raise PlanError('population_aggregate_cycle','/population/filter','A group frequency cannot define its own counting population; use a separately validated cohort')
            table=plan['grain']['table']
            if table not in self.tables or plan['population']['table']!=table:raise PlanError('population_table','/population','Population and grain must use the same table')
            keys=plan['grain']['keys']
            if keys!=self.tables[table]['primary_key']:raise PlanError('grain_key','/grain/keys','Complete declared output row key required')
            for c in plan['group_by']:
                if c['table']!=table or (table,c['column'])not in self.columns:raise PlanError('group_column','/group_by','Unknown grouping column')
                self.infer({'op':'column','table':table,'column':c['column']},table,'/group_by')
            typ,unit=self.infer(plan['expression'],table)
            if plan['population']['filter'] is not None and self.infer(plan['population']['filter'],table,'/population/filter')[0]!='boolean':raise PlanError('population_type','/population/filter','Boolean population filter required')
            missing=plan['missing_policy'];expected={'input','empty_group','denominator_zero','child_missing','unknown_predicate'}
            if set(missing)!=expected:raise PlanError('missing_policy','/missing_policy','AST3 requires explicit per-operation policies')
            if missing['input']not in {'propagate','count_missing'}:raise PlanError('input_policy','/missing_policy/input','Allowed propagate/count_missing')
            if missing['empty_group']!=('zero' if self.aggregates else None):raise PlanError('empty_group','/missing_policy/empty_group','Only actual aggregates use zero; column reads use null')
            if missing['denominator_zero']!=('missing' if self.division else None):raise PlanError('denominator_zero','/missing_policy/denominator_zero','Division by zero is unavailable, never false/zero')
            if missing['child_missing']not in {'propagate','skip'} or missing['unknown_predicate']!='defer':raise PlanError('aggregation_policy','/missing_policy','Missing child values explicit; unknown predicates defer')
            identity={k:deepcopy(plan[k]) for k in required-{'id'}};identity.update({'calculation_policy':VERSION,'inferred_type':typ,'unit':unit,'source_columns':sorted(self.sources),'relationships':sorted(self.used_relations)})
            return {'passed':True,'definition_status':'valid','definition_sha256':digest(identity),'proposal_sha256':digest(plan),'inferred_type':typ,'unit':unit,'identity':identity,'capture_missing':sorted(self.capture_missing),'aggregate_relationships':sorted(self.aggregate_relations),'errors':[]}
        except (PlanError,KeyError,TypeError) as error:
            return {'passed':False,'definition_status':'invalid','definition_sha256':None,'proposal_sha256':digest(plan),'errors':[{'code':getattr(error,'code','structure'),'path':getattr(error,'path','/'),'message':str(error)}]}

    def evaluate(self,node,row,table,policy,variables=None):
        op=node['op']
        if op=='variable':return (variables or {})[node['id']]
        if op=='column':return self.column(row,node['table'],node['column'])
        if op=='literal':return Decimal(str(node['value'])) if type(node['value'])in {int,float} else node['value']
        if op in {'child_count','conditional_count','child_sum','parent_match_count'}:
            r=self.relations[node['relationship_id']];rid=r['id'];parent=op!='parent_match_count'
            key=self.key(row,r['to_table'] if parent else r['from_table'],r['to_columns'] if parent else r['from_columns'])
            if key is None or not parent and any(v in r.get('no_reference_values',[]) for v in key):return None
            if not parent:return len(self.parent_index[rid].get(key,[]))
            if len(self.parent_index[rid].get(key,[]))!=1:return None
            children=self.child_index[rid].get(key,[])
            selected=[]
            for child in children:
                selected_flag=self.evaluate(node['where'],child,r['from_table'],policy) if node.get('where')is not None else True
                if selected_flag is None:return None
                if selected_flag is True:selected.append(child)
            if op in {'child_count','conditional_count'}:return len(selected)
            values=[self.evaluate(node['value'],c,r['from_table'],policy) for c in selected]
            if any(v is None for v in values) and policy['child_missing']=='propagate':return None
            if values and all(v is None for v in values):return None # All unavailable values are not an empty group.
            return sum((v for v in values if v is not None),Decimal(0))
        if op=='group_count':
            key=self.key(row,table,node['keys'])
            if key is None:return None
            population=self.current_population if table==self.current_table else None
            signature=(table,digest(node),digest(population))
            if signature not in self.group_cache:
                counts=Counter();unavailable=set()
                for other in self.rows[table]:
                    other_key=self.key(other,table,node['keys'])
                    if other_key is None:continue
                    population_flag=self.evaluate(population,other,table,policy) if population is not None else True
                    if population_flag is False:continue
                    flag=self.evaluate(node['where'],other,table,policy) if node['where'] is not None else True
                    if population_flag is None:flag=None
                    if flag is None:unavailable.add(other_key)
                    if flag is True:counts[other_key]+=1
                self.group_cache[signature]=(counts,unavailable)
            counts,unavailable=self.group_cache[signature]
            return None if key in unavailable else counts[key]
        values=[self.evaluate(a,row,table,policy,variables) for a in node.get('args',[])]
        if op=='is_missing':return values[0] is None
        if any(v is None for v in values):return None
        if op=='subtract':return values[0]-values[1]
        if op=='divide':return None if values[1]==0 else values[0]/values[1]
        if op=='and':return values[0] and values[1]
        if op=='datetime_diff':
            if (values[0].tzinfo is None)!=(values[1].tzinfo is None):return None
            delta=values[0]-values[1];us=Decimal((delta.days*86400+delta.seconds)*1000000+delta.microseconds)
            return us/{'us':Decimal(1),'ms':Decimal(1000),'s':Decimal(1000000)}[node['unit']]
        if op=='convert_unit':
            scales={'us':Decimal(1),'ms':Decimal(1000),'s':Decimal(1000000)}
            return values[0]*scales[node['from_unit']]/scales[node['to_unit']]
        return {'eq':lambda:values[0]==values[1],'lt':lambda:values[0]<values[1],'lte':lambda:values[0]<=values[1],
                'gt':lambda:values[0]>values[1],'gte':lambda:values[0]>=values[1]}[op]()

    def measure(self,plan,*,retain_values=False):
        checked=self.validate(plan);result={'version':VERSION,'candidate_id':plan.get('id'),'proposal_sha256':digest(plan),
            'definition_status':checked['definition_status'],'definition_sha256':checked['definition_sha256'],'validation':checked,
            'measurement_status':'rejected','summary':None,'errors':checked['errors'],'anomaly_execution':'not_run'}
        if not checked['passed']:return result
        if checked['capture_missing']:
            return dict(result,measurement_status='deferred',errors=[{'code':'capture_evidence','path':'/expression','message':'Complete captured relationship proof missing'}])
        required_tables={plan['grain']['table']}|{t for t,c in checked['identity']['source_columns']}
        if any(self.tables[t].get('csv_source')is None for t in required_tables):
            return dict(result,measurement_status='deferred',errors=[{'code':'schema_only','path':'/source_version','message':'Valid definition; observed source data unavailable'}])
        for rid in checked['aggregate_relationships']:
            r=self.relations[rid]
            if any(len(v)!=1 for v in self.parent_index[rid].values()):
                return dict(result,measurement_status='deferred',errors=[{'code':'parent_ambiguous','path':'/expression','message':'Duplicate parents prevent unique child aggregation'}])
        table=plan['grain']['table'];self.current_table=table;self.current_population=plan['population']['filter'];values=[];keys=[];unavailable=0;grouped=defaultdict(list)
        with source_scope(self.inventory,self.policy,relationship_capture_scope=self.capture):
            # Recheck all original bytes at actual measurement time, even when
            # typed rows are cached in this bounded engine instance.
            read_dataset(self.inventory,project_root=ROOT,max_rows=10000)
        with localcontext() as context:
            context.prec=50
            for row in self.rows[table]:
                selected=self.evaluate(plan['population']['filter'],row,table,plan['missing_policy']) if plan['population']['filter']is not None else True
                if selected is None:unavailable+=1;continue
                if selected is not True:continue
                value=self.evaluate(plan['expression'],row,table,plan['missing_policy'])
                values.append(value);keys.append(self.key(row,table,plan['grain']['keys']))
                if plan['group_by']:
                    group=tuple(None if self.missing(row.get(c['column']),table,c['column']) else row.get(c['column']) for c in plan['group_by'])
                    grouped[group].append(value)
        summary=native._summary(values);summary.update({'population_rows_before_selection':len(self.rows[table]),'selection_unavailable_count':unavailable,'source_rows_preserved':True})
        if 'datetime_diff' in str(plan['expression']):summary['datetime_limitations']={'calculation':'parsed_timestamp_coordinate_difference','business_duration_contract':'unknown','common_producer_clock':'unknown'}
        groups=[{'group_values':list(key),'summary':native._summary(v)} for key,v in sorted(grouped.items(),key=lambda x:str(x[0]))]
        result.update(measurement_status='measured',summary=summary,groups=groups,errors=[],measurement_sha256=digest({'definition':checked['definition_sha256'],'summary':summary,'groups':groups}))
        if retain_values:result['_values']=values;result['_keys']=keys
        return result

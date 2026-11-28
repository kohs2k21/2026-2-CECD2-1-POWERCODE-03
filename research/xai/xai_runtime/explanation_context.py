"""Versioned human-readable sidecar; original evidence and decisions stay intact."""
from copy import deepcopy
from .artifacts import digest, require
from .engine import validate_packet
from .grounded_followup import allowed_followups, render_grounded

VERSION='xai-explanation-context/1.0.0'
TABLES={'EC_TRANSACTION_MONITORING':'T','EC_PROCESS_MONITORING':'P','EC_MESSAGE_MONITORING':'M'}
NAMES={'T':'거래','P':'프로세스','M':'메시지'}
OPERATORS=dict(gt='초과',ge='이상',lt='미만',le='이하',eq='같음',ne='다름')
STATES=dict(warning='경고',nonwarning='비경고',unavailable='평가 불가',context='참고')


def label(expr, relationships):
    op=expr['op'];args=expr.get('args',[])
    if op=='column':return TABLES.get(expr['table'],expr['table'])+'.'+expr['column']
    if op=='literal':return repr(expr['value'])
    if op in ('child_count','conditional_count','parent_match_count'):
        rel=relationships[expr['relationship_id']]
        child=TABLES.get(rel['from_table'],rel['from_table']);parent=TABLES.get(rel['to_table'],rel['to_table'])
        keys='·'.join('RETRY_COUNT(원문)'if c=='__RETRY_LEXICAL'else c for c in rel['from_columns'])
        if op=='parent_match_count':return f'{NAMES.get(child,child)} 행과 {keys}가 일치하는 {NAMES.get(parent,parent)} 행 수'
        text=f'{NAMES.get(parent,parent)} 행에 {keys}로 연결된 {NAMES.get(child,child)} 행 수'
        if op=='conditional_count':text+=' 중 ['+label(expr['where'],relationships)+'] 조건을 만족하는 행 수'
        return text
    if op=='is_missing':return label(args[0],relationships)+' 결측 여부'
    if op=='datetime_diff':return '('+label(args[0],relationships)+' − '+label(args[1],relationships)+') 시간 차이'
    symbols=dict(subtract='−',divide='÷',add='+',multiply='×',eq='=',ne='≠',gt='>',ge='≥',lt='<',le='≤',and_='AND',or_='OR')
    if op in symbols:return '('+(' '+symbols[op]+' ').join(label(a,relationships) for a in args)+')'
    return op+'('+', '.join(label(a,relationships) for a in args)+') [미확인 연산명 보존]'


def dependencies(expr,relationships):
    fields=set();rels={}
    def visit(node):
        if not isinstance(node,dict):return
        if node.get('op')=='column':fields.add(TABLES.get(node['table'],node['table'])+'.'+node['column'])
        if 'relationship_id' in node:
            rel=relationships[node['relationship_id']];rels[rel['id']]=deepcopy(rel)
            for side in ('from','to'):
                fields.update(TABLES.get(rel[side+'_table'],rel[side+'_table'])+'.'+c for c in rel[side+'_columns'])
        for v in node.values():
            if isinstance(v,dict):visit(v)
            elif isinstance(v,list):
                for z in v:visit(z)
    visit(expr)
    return dict(fields=sorted(fields),relationships=list(rels.values()))


def locations(packet,fact):
    source={s['source_id']:s for s in packet['source_refs']}
    return [dict(source_id=sid,table=source[sid]['table'],physical_row=source[sid]['source_row_id'],
                 transaction_id=source[sid]['transaction_ref'],process_id=source[sid]['process_ref'],
                 message_id=source[sid]['message_ref'],snapshot_sha256=source[sid]['snapshot_sha256']) for sid in fact['source_refs']]


def position_text(locs):
    return '; '.join(f"{NAMES.get(s['table'],s['table'])} {s['table']} 원천행 {s['physical_row']}"+
                     (f" / PROCESS_ID={s['process_id']}" if s['process_id'] else '')+
                     (f" / MESSAGE_ID={s['message_id']}" if s['message_id'] else '') for s in locs)


def build_context(packet,audit,definition,source_rows=None):
    validate_packet(packet)
    require(audit['packet_id']==packet['packet_id'],'context_audit_packet_mismatch')
    relationships={r['id']:r for r in definition['schema']['relationships']}
    features={f['id']:f['feature'] for f in definition['features']}
    result={}
    for fact in packet['facts']:
        p=fact['payload'];meta=audit['fact_metadata'].get(fact['id'],{});feature=meta.get('feature')
        if not feature and isinstance(meta.get('actual_rule'),dict):feature=features.get(meta['actual_rule']['feature_id'])
        if not feature and p.get('feature_id') in features:feature=features[p['feature_id']]
        if fact['kind']=='ratio_alternative':
            if isinstance(meta.get('actual_rule'),dict):feature=features[meta['actual_rule']['feature_id']]
            elif isinstance(meta.get('actual_rule'),str) and meta['actual_rule']=='fallback:counter_partition_consistency':
                short=locations(packet,fact)[0]['table']
                feature=dict(expression=dict(op='subtract',args=[dict(op='column',table=short,column='TOTAL_COUNT'),dict(op='add',args=[dict(op='column',table=short,column=c) for c in ('SUCCESS_COUNT','ERROR_COUNT')])]),unit='count')
        expr=feature.get('expression') if feature else None
        rule=meta.get('actual_rule') if isinstance(meta.get('actual_rule'),dict) else meta.get('rule',{})
        if feature and feature.get('sha'):
            canonical_feature=next((v for v in features.values()if v['sha']==feature['sha']),None)
            if canonical_feature is not None:require(digest(feature)==digest(canonical_feature),'feature_metadata_not_frozen_definition')
        title=label(expr,relationships) if expr else p.get('approved_statement',p.get('feature_name',p.get('check_name',p.get('field_name',p.get('algorithm','검사')))))
        locs=locations(packet,fact)
        if source_rows is not None:
            for loc in locs:
                raw=source_rows[loc['table']][loc['physical_row']]
                require(raw.get('TRANSACTION_ID') in ('',loc['transaction_id']),'source_values_cross_transaction')
                loc['observed_state_fields']={k:None if raw[k]=='' else raw[k]for k in ('STATUS','RETRY_COUNT')if k in raw}
        desc=dict(evidence_id=fact['id'],kind=fact['kind'],state=fact['state'],title=title,
                  locations=locs,calculation=deepcopy(expr),definition_sha256=feature.get('sha') if feature else None,
                  dependencies=dependencies(expr,relationships) if expr else dict(fields=[],relationships=[]),
                  missing_policy=deepcopy(feature.get('missing_policy')) if feature else None,
                  baseline=deepcopy(rule.get('baseline')) if rule else None,
                  analysis_unit=(feature.get('grain') if feature else None),population=(feature.get('population') if feature else None))
        if fact['kind']=='unavailable':
            fields=p['missing_fields'];field_text='、'.join(fields) if fields else '필요한 원천·관계·참조 정보'
            desc['text']=f"{position_text(locs)}: {title} 검사는 {field_text} 확인이 필요하여 평가 불가입니다. 비경고로 처리하지 않았습니다."
        elif fact['kind'] in ('rule_comparison','ratio_alternative'):
            value=p.get('observed',p.get('alternative_value'));unit=p['unit']['symbol'] if p['unit']['status']=='known' else '(단위 미확인)'
            desc['text']=f"{position_text(locs)}: {title} = {value}{unit}; 적용 조건 {p['threshold']}{unit} {OPERATORS[p['operator']]}; 판정 {STATES[fact['state']]}."
            if fact['kind']=='ratio_alternative':desc['text']+=' 분모 0으로 원래 비율 대신 명시된 대체 검사를 적용했습니다.'
            baseline=desc['baseline']
            if baseline:
                period=baseline.get('reference_period',{})
                desc['text']+=f" 참조: {baseline.get('reference_split','미확인')} {period.get('start_inclusive','미확인')}~{period.get('end_exclusive','미확인')}(끝 제외), p{baseline.get('quantile',0)*100:g}, 유효 표본 {baseline.get('reference_valid_n','미확인')}건. 동결된 연구용 참조이며 운영 적합성과 업무 위반 여부는 미확인입니다."
        elif fact['kind']=='logical_rule':
            observed=[loc['table']+'.'+k+'='+str(v)for loc in locs for k,v in loc.get('observed_state_fields',{}).items()]
            desc['text']=f"{position_text(locs)}: {title}; 관측 값 {', '.join(observed) if observed else p['matched_values']}; 판정 {STATES[fact['state']]}. 복구 여부와 업무 위반 여부는 별도 확인이 필요합니다."
        elif fact['kind']=='ml_score':
            desc['text']=f"{position_text(locs)}: {p['algorithm']} 점수 {p['score']}; 적용 조건 {p['threshold']} {OPERATORS[p['operator']]}; 판정 {STATES[fact['state']]}. 점수가 클수록 이상입니다."
        else:desc['text']=f"{position_text(locs)}: {p.get('value',title)}"
        result[fact['id']]=desc
    return dict(contract=VERSION,packet_sha256=digest(packet),definition_sha256=digest(definition),facts=result)


def semantic_key(fact,context):
    p=fact['payload'];c=context['facts'][fact['id']]
    if fact['kind'] in ('rule_comparison','ratio_alternative'):
        return dict(kind='numeric',source=sorted(fact['source_refs']),calculation=c['calculation'],
                    observed=p.get('observed',p.get('alternative_value')),operator=p['operator'],threshold=p['threshold'],unit=p['unit'])
    return dict(kind=fact['kind'],source=sorted(fact['source_refs']),payload=p)


def groups(packet,context):
    require(context['packet_sha256']==digest(packet),'context_packet_changed')
    result={}
    for fact in packet['facts']:
        if fact['state'] not in ('warning','unavailable') and fact['kind']!='observation':continue
        key=semantic_key(fact,context);gid='G'+digest(key)[:20]
        group=result.setdefault(gid,dict(id=gid,evidence_ids=[],kind='numeric' if key['kind']=='numeric' else fact['kind'],
            state=fact['state'],source_refs=sorted(fact['source_refs']),title=context['facts'][fact['id']]['title']))
        require(group['state']==fact['state'],'duplicate_semantics_conflicting_state')
        group['evidence_ids'].append(fact['id'])
    for g in result.values():g['evidence_ids'].sort()
    return sorted(result.values(),key=lambda g:(rank(g),g['title'],g['source_refs'],g['id']))


def rank(group):
    if group['state']=='unavailable':return 0
    return dict(logical_rule=1,numeric=2,ml_score=3,observation=4).get(group['kind'],5)


def choose_groups(allgroups,shortlisted=None):
    # Optional model shortlist never erases full detail or required state coverage.
    candidates=[g for g in allgroups if shortlisted is None or set(g['evidence_ids'])&set(shortlisted)]
    chosen=[];seen=set()
    for g in candidates:
        k=rank(g)
        if k not in seen:chosen.append(g);seen.add(k)
    for k in {rank(g)for g in allgroups}:
        if k not in seen:chosen.append(next(g for g in allgroups if rank(g)==k));seen.add(k)
    return sorted(chosen,key=lambda g:(rank(g),g['title'],g['id']))


def render_explained(packet,policy,context,ml=None,shortlisted=None,execution='code'):
    validate_packet(packet);allgroups=groups(packet,context);selected=choose_groups(allgroups,shortlisted)
    base=render_grounded(packet,policy)
    sentences=[]
    for s in base['sentences']:
        c=context['facts'][s['evidence_id']]
        sentences.append(dict(evidence_id=s['evidence_id'],source_refs=s['source_refs'],locations=c['locations'],text=c['text']))
    checks=[]
    for spec in allowed_followups(packet['facts']):
        ids=spec['evidence_ids'];cs=[context['facts'][i]for i in ids]
        positions=sorted({position_text(c['locations'])for c in cs})
        if spec['code']=='review_input_completeness':
            targets=[]
            for fid in ids:
                fact=next(f for f in packet['facts']if f['id']==fid)
                targets.append(position_text(context['facts'][fid]['locations'])+' → '+','.join(fact['payload']['missing_fields'] or [context['facts'][fid]['title']]))
            text='결측·평가 불가 확인: '+'; '.join(targets)+'. 해당 기록의 종료/수집 완료 여부를 확인하세요.'
        elif spec['code']=='review_recovery_history':text='; '.join(positions)+': 최종 성공과 하위 실패가 함께 관측됐습니다. 후속 프로세스·재시도·복구 기록을 확인하세요. 복구 실패는 확정하지 않습니다.'
        elif spec['code']=='review_source_operation':text='; '.join(positions)+': 경고의 연결 키와 실제 원천 기록을 확인하세요. 허용되는 업무 예외인지는 미확인입니다.'
        elif spec['code']=='review_rule_threshold':text='; '.join(positions)+': 상세 근거에 표시한 계산식·단위·참조 기간·모집단이 현재 적용 범위와 맞는지 확인하세요.'
        else:text='; '.join(positions)+': ML 입력 민감도와 원천 피처를 함께 확인하세요. 민감도는 원인이나 피처별 기여도의 합을 뜻하지 않습니다.'
        checks.append(dict(code=spec['code'],evidence_ids=ids,source_refs=sorted({s for c in cs for s in [x['source_id']for x in c['locations']]}),text=text,priority=spec['priority']))
    unknown={f['id']for f in packet['facts']if f['required'] and f['state']=='unavailable'}
    require(unknown<={i for c in checks if c['code']=='review_input_completeness'for i in c['evidence_ids']},'specific_check_lost_unknown')
    names=dict(logical_rule='논리 SR',numeric='수치 SR',ml_score='ML')
    state_names=sorted({names.get(g['kind'],g['kind'])for g in allgroups if g['state']=='warning'})
    narrative=(f"동일 거래에서 {', '.join(state_names)} 경고가 함께 관측됐습니다. 각 조건의 동시 관측이며 서로의 원인임을 확정하지 않습니다." if state_names else
               '현재 저장된 검사에서 경고가 없습니다. 평가 불가와 입력 가용성은 상세 근거에서 별도로 확인해야 합니다.')
    if unknown:narrative+=' 평가 불가 항목이 있어 로그 완결성과 입력 필드를 함께 확인해야 합니다.'
    result=dict(contract='xai-explained-result/2.0.0',packet_id=packet['packet_id'],input_packet_sha256=digest(packet),
        transaction=deepcopy(packet['scope']),decision=deepcopy(packet['decision']),severity=deepcopy(packet['severity']),
        severity_interpretation=dict(type='relative_statistical_grade',operational_impact='unconfirmed',text=base['severity_explanation']['text']),
        execution=execution,narrative=narrative,summary_groups=selected,all_groups=allgroups,details=sentences,
        next_checks=checks,ml_explanations=ml or {},cause_status='unconfirmed',
        coverage=dict(total_facts=len(packet['facts']),required_facts=sum(f['required']for f in packet['facts']),
                      detailed_required_ids=sorted(f['id']for f in packet['facts']if f['required']),
                      adverse_facts=sum(f['state']in('warning','unavailable')for f in packet['facts']),
                      grouped_facts=sum(len(g['evidence_ids'])for g in allgroups)))
    validate_explained(packet,context,result)
    return result


def validate_explained(packet,context,result):
    require(result['input_packet_sha256']==digest(packet)==context['packet_sha256'],'stale_explanation_binding')
    require(result['decision']==packet['decision'] and result['severity']==packet['severity'],'explanation_changed_decision_or_grade')
    expected={f['id']for f in packet['facts']if f['required']};actual=[s['evidence_id']for s in result['details']]
    require(len(actual)==len(set(actual)) and expected<=set(actual),'required_detail_omitted_or_duplicated')
    require(all(s['text']==context['facts'][s['evidence_id']]['text'] and s['locations']==context['facts'][s['evidence_id']]['locations'] for s in result['details']),'unsupported_explanation_text_or_location')
    groupids=[i for g in result['all_groups']for i in g['evidence_ids']]
    adverse={f['id']for f in packet['facts']if f['state']in('warning','unavailable')}
    require(len(groupids)==len(set(groupids)) and adverse<=set(groupids),'grouping_lost_evidence')

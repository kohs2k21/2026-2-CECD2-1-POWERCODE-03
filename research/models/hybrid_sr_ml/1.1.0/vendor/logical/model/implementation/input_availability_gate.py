"""Prevent required join/group inputs from being filtered into apparent normality."""
POLICY="required-input-availability1.0"

def errors(proposal):
    found=[]
    for c in (proposal or {}).get('candidates',[]):
        p=c.get('plan')
        if not p:continue
        required=set()
        if p['kind']=='unexplained_event':required={j['source']for j in p['context']['join']}
        elif p['kind']=='duplicate_key':required=set(p['keys'])
        elif p['kind']=='relation_check':required={j['source']for j in p['related']['join']}
        for a in p['select']['filter']:
            if a['op']=='is_present'and a['column']in required:
                found.append({'candidate':c['id'],'error':'Required root join/group input '+a['column']+' cannot be filtered away by is_present. Preserve missing required input as unavailable, not nonwarning. Put known business population predicates in select; do not add an availability filter.'})
    return found

def apply(result,proposal):
    if not result['executable_valid']:return result
    bad=errors(proposal)
    if not bad:return {**result,'input_availability_policy':POLICY}
    return {**result,'executable_valid':False,'decision':'invalid','errors':bad,'rules':[],
            'input_availability_policy':POLICY}

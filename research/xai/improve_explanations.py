"""Portable verified-context projection; historical data preparation is excluded."""
from copy import deepcopy
from xai_runtime.artifacts import digest

def projected_view(packet,context,probes,facts,index,count):
    used={sid for f in facts for sid in f['source_refs']}
    cards=[]
    for f in facts:
        card=deepcopy(f);c=context['facts'][f['id']]
        card['verified_context']=dict(title=c['title'],locations=c['locations'],input_fields=c['dependencies']['fields'],
            reference_period=c['baseline'].get('reference_period')if c['baseline']else None)
        if f['id']in probes:
            pr=probes[f['id']]
            card['ML_input_sensitivity']=dict(top_positive_changes=pr['top_positive_changes'],
                    meaning='Reference replacement sensitivity only; not additive attribution, cause or a physically reconstructed log.')
        cards.append(card)
    return dict(contract='xai-summary-input/1.0.0',evidence_binding_sha256=digest(dict(packet=packet['packet_id'],facts=facts)),
                parent_packet_sha256=digest(packet),decision=packet['decision']['state'],facts=cards,
                source_locations=[s for s in packet['source_refs']if s['source_id']in used],chunk=dict(index=index,count=count),
                note='This chunk contains a complete, bounded subset. Other chunks and all mandatory detail are preserved. Do not infer absent facts or business causes.')

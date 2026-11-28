"""Stricter decoding for a bounded retry; semantic acceptance stays unchanged."""
from copy import deepcopy

from .grounded_followup import CHUNK_SCHEMA, allowed_followups


def constrained_chunk_schema(view):
    schema = deepcopy(CHUNK_SCHEMA)
    schema['properties']['packet_id'] = dict(const=view['parent_packet_id'])
    alternatives = []
    for fact in view['facts']:
        alternatives.append(dict(type='object', properties=dict(template_id=dict(const=fact['kind']),
            evidence_id=dict(const=fact['id'])), required=['template_id', 'evidence_id'], additionalProperties=False))
    schema['properties']['items']['items'] = dict(anyOf=alternatives)
    actions = []
    for spec in allowed_followups(view['facts']):
        actions.append(dict(type='object', properties=dict(code=dict(const=spec['code']),
            priority=dict(const=spec['priority']), evidence_ids=dict(type='array', minItems=1,
                maxItems=len(spec['evidence_ids']), uniqueItems=True, items=dict(enum=spec['evidence_ids']))),
            required=['code', 'evidence_ids', 'priority'], additionalProperties=False))
    if actions:
        schema['properties']['next_checks']['items'] = dict(anyOf=actions)
    else:
        schema['properties']['next_checks']['maxItems'] = 0
    return schema

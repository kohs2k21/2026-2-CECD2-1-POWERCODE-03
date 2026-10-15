"""Shared CSV/API projection. Only documented metadata; no guessed runtime logs."""
import csv
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from collector.replay_csv import _canonical_header
from collector.storage import project_observation

ALIASES={'process_dt':'process_datetime_raw','process_hub':'process_hub_id'}
LAYERS=('T','P','M','B')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def normalize(layer,raw):
    if layer not in LAYERS or not isinstance(raw,dict):raise ValueError('input_record_contract_rejected')
    fields={ALIASES.get(_canonical_header(k),_canonical_header(k)):(None if v=='\\N' else v)
        for k,v in raw.items() if isinstance(k,str)}
    if layer=='B':
        # Explicitly track a body reference; raw message values never enter the model or staging table.
        value={'message_id':fields.get('message_id')}
        if not isinstance(value['message_id'],str) or not value['message_id'].strip():raise ValueError('body_message_id_required')
    else:
        fields.pop('response_message',None)
        value=project_observation(layer,fields)[0]
    if not value or not any(v is not None for v in value.values()):raise ValueError('input_has_no_recognized_fields')
    return value


def identity(layer,value):
    keys={'T':('transaction_id',),'P':('transaction_id','process_id'),
          'M':('transaction_id','process_id','message_id'),'B':('message_id',)}[layer]
    parts=[]
    for key in keys:
        v=value.get(key)
        if not isinstance(v,str) or not v.strip():return None
        parts.append(v)
    if layer!='B':
        retry=value.get('retry_count')
        if retry is None:parts.append('unknown')
        else:
            try:
                number=Decimal(str(retry))
                if isinstance(retry,bool) or not number.is_finite() or number<0 or number!=number.to_integral_value():return None
                parts.append(str(int(number)))
            except (InvalidOperation,ValueError,TypeError):return None
    return parts


def csv_records(layer,path):
    csv.field_size_limit(64*1024*1024)
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream,strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames):
            raise ValueError('csv_headers_rejected')
        for row in reader:
            if None in row or any(v is None for v in row.values()):raise ValueError('csv_column_count_rejected')
            yield normalize(layer,row)


def at(value,path):
    """A bounded list of literal JSON keys; no eval, queries or external requests."""
    if not isinstance(path,list) or len(path)>12 or not all(isinstance(k,str) and 0<len(k)<=128 for k in path):
        raise ValueError('api_mapping_path_rejected')
    for key in path:
        if not isinstance(value,dict) or key not in value:return None
        value=value[key]
    return value


def mapped_api_records(values,mapping=None):
    """Default: explicitly tagged normalized records. Other real shapes use a mapping file.

    Mapping streams use paths relative to each responseObject item; [] means the item.
    A path can select an object/list. fields map DB columns to paths relative to that
    selected record; inherit maps DB columns to paths relative to its outer item.
    """
    if not isinstance(values,list):raise ValueError('api_log_list_required')
    if len(values)>100000:raise ValueError('api_log_limit_exceeded')
    for outer in values:
        if not isinstance(outer,dict):raise ValueError('api_log_object_required')
        if mapping is None:
            if set(outer)!= {'layer','values'} or outer['layer'] not in LAYERS:
                raise ValueError('api_log_mapping_required')
            yield outer['layer'],normalize(outer['layer'],outer['values'])
            continue
        streams=mapping.get('streams') if isinstance(mapping,dict) else None
        if not isinstance(mapping,dict) or mapping.get('version')!=1 or not isinstance(streams,list) or not 1<=len(streams)<=12:
            raise ValueError('api_mapping_contract_rejected')
        emitted=0
        for spec in streams:
            if not isinstance(spec,dict) or spec.get('layer') not in LAYERS or set(spec)-{'layer','path','fields','inherit'}:
                raise ValueError('api_mapping_stream_rejected')
            selected=at(outer,spec.get('path',[]))
            selected=selected if isinstance(selected,list) else [] if selected is None else [selected]
            for item in selected:
                if not isinstance(item,dict):raise ValueError('api_mapped_record_rejected')
                fields=spec.get('fields',{})
                inherited=spec.get('inherit',{})
                if not isinstance(fields,dict) or not isinstance(inherited,dict):raise ValueError('api_mapping_fields_rejected')
                raw={key:at(item,path) for key,path in fields.items()} if fields else dict(item)
                raw.update({key:at(outer,path) for key,path in inherited.items()})
                yield spec['layer'],normalize(spec['layer'],raw)
                emitted+=1
                if emitted>100000:raise ValueError('api_log_limit_exceeded')
        if not emitted:raise ValueError('api_mapping_matched_no_records')

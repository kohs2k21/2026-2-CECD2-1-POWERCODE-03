"""Schema-bound decoding and event-grain explanation plans.

Physical source/type bindings constrain syntax, never the right business condition.
Original AST and compact-plan runs remain preserved and are not overwritten.
"""
import json
from copy import deepcopy
import jsonschema
import typed_plan as base
from logic_engine import inventory,source_types,obj,STR
from citation_spans import quote_pool
POLICY="source-bound-reusable-discovery1.1-conditional-row"
SCHEMA_PATCH="empty-source-schema1.0.1"
CITATION_POLICY="exact-input-spans1.1"
PURPOSE="""
This is OFFLINE REUSABLE RULE RECOMMENDATION, not just classifying the supplied sample.
Propose checks supported by the documents EVEN WHEN EVERY CURRENT ROW IS VALID.
No-candidate is appropriate when no check is justified, not merely because no violation is observed.
For unexplained-event review, the ROOT select is the problematic EVENT table, not the parent table.
context links that event to its qualifying parent/context. Every explained_by relation links
FROM THE ROOT EVENT to a possible handling/recovery row. Use all documented scope/key components.
A null explanation pointer denotes no relationship when the source document says so; it is not a business violation.
The grammar supplies physical tables and columns, not the correct rule, joins, role or business-code values.
Choose those from the source documents. Do not invent requirements from logical relationship definitions.
"""
SYSTEM=base.SYSTEM.replace(
    "unexplained_match: select,events:{select,join},explained_by:[{select,join}].\nFires when an outer row has a matching event for which NONE of the documented explanation relations exists.\nEach explained_by join source is a column of that EVENT, target is a column of the explanation row.",
    "unexplained_event: select,context:{select,join},explained_by:[{select,join}].\nRoot select is the event needing review. Context links this event to its qualifying parent.\nFires when the context qualifies and NONE of the documented explanation relations exists.\nEvery relation source is a column of the ROOT EVENT, target is a column of the related row."
)+PURPOSE

def schema_for(case):
    cols={t:c for t,c in inventory(case).items() if c};types=source_types(case);identifiers=set()
    for t,d in case["schema"].items():
        identifiers.update((t,k)for k in d.get("keys",[]))
        for k,v in d.items():
            if isinstance(v,str)and"."in v:
                identifiers.add((t,k));identifiers.add(tuple(v.split(".",1)))
    def selector(table):
        atoms=[]
        allcols=cols[table]
        ordinary=[c for c in allcols if(table,c)not in identifiers]
        if ordinary:
            atoms.append(obj({"column":{"enum":ordinary},"op":{"enum":["eq","ne"]},
                              "value":{"type":["string","number","boolean"]}}))
            atoms.append(obj({"column":{"enum":ordinary},"op":{"enum":["in","not_in"]},
                              "value":{"type":"array","minItems":1,"maxItems":32,"items":{"type":["string","number","boolean"]}}}))
            numeric=[c for c in ordinary if types[table,c]=="number"]
            if numeric:atoms.append(obj({"column":{"enum":numeric},"op":{"enum":["lt","le","gt","ge"]},"value":{"type":"number"}}))
        atoms.append(obj({"column":{"enum":allcols},"op":{"enum":["is_missing","is_present"]},"value":{"type":"null"}}))
        return obj({"table":{"const":table},"filter":{"type":"array","maxItems":6,"items":{"oneOf":atoms}}})
    def relation(source,target):
        return obj({"select":selector(target),"join":{"type":"array","minItems":1,"maxItems":6,
            "items":obj({"source":{"enum":cols[source]},"target":{"enum":cols[target]}})}})
    plans=[]
    for table in cols:
        plans.extend([
            obj({"kind":{"const":"row_check"},"select":selector(table)}),
            obj({"kind":{"const":"conditional_row_check"},"select":selector(table),
                 "violation":{**selector(table)["properties"]["filter"],"minItems":1}}),
            obj({"kind":{"const":"duplicate_key"},"select":selector(table),
                 "keys":{"type":"array","minItems":1,"maxItems":6,"items":{"enum":cols[table]}}}),
            obj({"kind":{"const":"relation_check"},"select":selector(table),
                 "related":{"oneOf":[relation(table,target)for target in cols]},
                 "violation":{"enum":["required_match_missing","match_present","match_not_exactly_one"]}}),
            obj({"kind":{"const":"unexplained_event"},"select":selector(table),
                 "context":{"oneOf":[relation(table,target)for target in cols]},
                 "explained_by":{"type":"array","minItems":1,"maxItems":4,
                     "items":{"oneOf":[relation(table,target)for target in cols]}}})
        ])
        numeric=[c for c in cols[table]if types[table,c]=="number"]
        if numeric:
            plans.append(obj({"kind":{"const":"dependency_order"},"select":selector(table),
                "link":relation(table,table)["properties"]["join"],
                "parent_end":{"enum":numeric},"child_start":{"enum":numeric},"allow_equal":{"type":"boolean"}}))
    evidence=[]
    for d in case["documents"]:
        if len(d["text"])<=500:
            evidence.append(obj({"document_id":{"const":d["id"]},"quote":{"const":d["text"]}}))
        else:
            evidence.append(obj({"document_id":{"const":d["id"]},"quote":{"enum":quote_pool(d["text"])}}))
    common=deepcopy(base.COMMON)
    common["evidence"]=({"type":"array","minItems":1,"maxItems":8,"items":{"oneOf":evidence}}
                         if evidence else {"type":"array","maxItems":0,"items":base.COMMON["evidence"]["items"]})
    execute=obj({**common,"role":{"enum":["HR_candidate","observed_SR_candidate"]},
                 "plan":{"oneOf":plans},"needed":{"type":"array","maxItems":0,"items":STR}})
    hold=obj({**common,"role":{"const":"request_evidence"},"plan":{"type":"null"},
              "needed":{"type":"array","minItems":1,"maxItems":5,"items":STR}})
    variants=[execute,hold] if plans and evidence else [hold]
    return obj({"candidates":{"type":"array","maxItems":4,"items":{"oneOf":variants}},
                "no_candidate_reason":{"type":["string","null"],"maxLength":400}})

def messages(case):
    public={k:deepcopy(case[k])for k in("schema","documents","capture","records","question")}
    public["physical_column_inventory"]=inventory(case)
    return [{"role":"system","content":SYSTEM},{"role":"user","content":json.dumps(public,separators=(",",":"))}]

def validate_plan(case,plan):
    if plan["kind"]!="unexplained_event":return base.validate_plan(case,plan)
    cols,types=inventory(case),source_types(case)
    source=base.columns_of_select(plan["select"],cols,types)
    for rel in[plan["context"]]+plan["explained_by"]:
        target=base.columns_of_select(rel["select"],cols,types)
        for j in rel["join"]:
            if j["source"]not in cols[source]or j["target"]not in cols[target]:raise base.Invalid("Join column absent")
            a,b=types[source,j["source"]],types[target,j["target"]]
            if a!=b and"unknown"not in(a,b):raise base.Invalid("Join key type mismatch")
    return source

def execute_event(case,plan,requires_complete,matches_fn=None):
    match=matches_fn or (lambda source,rel:base.matches(case,source,rel))
    rows=[];table=plan["select"]["table"];complete=case["capture"].get("entity_family_complete")is True
    for index,event in enumerate(case["records"][table]):
        selected=base.filt(event,plan["select"]["filter"]);support=[]
        if selected is not True:value=selected
        else:
            ctx,uncertain=match(event,plan["context"])
            rawctx=deepcopy(plan["context"]);rawctx["select"]["filter"]=[]
            raw,raw_unknown=match(event,rawctx)
            support.extend((t,i)for t,i,r in raw)
            if len(raw)!=1 or raw_unknown or uncertain:value=None
            elif not ctx:value=False
            elif requires_complete and not complete:value=None
            else:
                found=[];unknown=False
                for rel in plan["explained_by"]:
                    matches,u=match(event,rel);found.extend(matches);unknown|=u
                support.extend((t,i)for t,i,r in found)
                value=False if found else None if unknown or not complete else True
        rows.append({"source_table":table,"source_row_index":index,"source_record":event,
                     "value":value,"support_rows":[{"table":t,"row_index":i}for t,i in sorted(set(support))]})
    return rows

def validate_and_execute(case,proposal):
    try:jsonschema.Draft202012Validator(schema_for(case)).validate(proposal)
    except(jsonschema.ValidationError,TypeError)as e:
        return {"policy":POLICY,"executable_valid":False,"errors":[str(e)[:800]],"rules":[],"decision":"invalid"}
    if not proposal["candidates"]and not proposal["no_candidate_reason"]:
        return {"policy":POLICY,"executable_valid":False,"errors":["No-candidate needs a specific reason"],"rules":[],"decision":"invalid"}
    docs={d["id"]:d for d in case["documents"]};errors=[];results=[];ids=set()
    for c in proposal["candidates"]:
        err=[];cid=c["id"]
        if cid in ids:err.append("Duplicate candidate id")
        ids.add(cid)
        for e in c["evidence"]:
            d=docs.get(e["document_id"])
            if not d or e["quote"]not in d["text"]:err.append("Exact source evidence missing")
            if c["role"]=="HR_candidate"and(not d or d["authority"]not in("controlled_evaluation_contract_not_company_policy","reviewed_business_contract","verified_DDL")):
                err.append("Monitoring note alone cannot authorize HR")
        p=c["plan"]
        if p is not None:
            try:validate_plan(case,p)
            except(base.Invalid,KeyError,TypeError)as e:err.append(str(e))
        if err:
            errors.extend({"candidate":cid,"error":x}for x in err);continue
        if p is None:rows=[];decision="unavailable"
        else:
            rows=execute_event(case,p,c["requires_complete_capture"])if p["kind"]=="unexplained_event"else base.execute_plan(case,p,c["requires_complete_capture"])
            value=base.any3([x["value"]for x in rows])
            decision="warning"if value is True else"unavailable"if value is None else"nonwarning"
        results.append({"id":cid,"role":c["role"],"decision":decision,"rows":rows,"semantic_review":"pending"})
    decision="invalid"if errors else"warning"if any(r["decision"]=="warning"for r in results)else"unavailable"if any(r["decision"]=="unavailable"for r in results)else"nonwarning"
    return {"policy":POLICY,"executable_valid":not errors,"errors":errors,"rules":results,"decision":decision,
            "semantic_entailment_proven_by_code":False,"operational_approval":False}

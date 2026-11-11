"""Compact typed relational plans; model selects bindings/meaning, code executes.
Operators are generic across names. No case IDs or target conditions are accepted.
"""
import json,hashlib
import jsonschema
from copy import deepcopy
from logic_engine import inventory,source_types,obj,STR
POLICY="typed-relational-discovery1.3-evidence-dominance"
SELECT=obj({"table":STR,"filter":{"type":"array","maxItems":6,"items":{"$ref":"#/$defs/atom"}}})
JOIN={"type":"array","minItems":1,"maxItems":6,"items":obj({"source":STR,"target":STR})}
ATOM=obj({"column":STR,"op":{"enum":["eq","ne","lt","le","gt","ge","in","not_in","is_missing","is_present"]},
          "value":{"type":["string","number","boolean","null","array"],"items":{"type":["string","number","boolean"]}}})
REL=obj({"select":SELECT,"join":JOIN})
PLANS=[
 obj({"kind":{"const":"row_check"},"select":SELECT}),
 obj({"kind":{"const":"conditional_row_check"},"select":SELECT,
      "violation":{"type":"array","minItems":1,"maxItems":6,"items":{"$ref":"#/$defs/atom"}}}),
 obj({"kind":{"const":"duplicate_key"},"select":SELECT,"keys":{"type":"array","minItems":1,"maxItems":6,"items":STR}}),
 obj({"kind":{"const":"relation_check"},"select":SELECT,"related":REL,
      "violation":{"enum":["required_match_missing","match_present","match_not_exactly_one"]}}),
 obj({"kind":{"const":"dependency_order"},"select":SELECT,"link":JOIN,
      "parent_end":STR,"child_start":STR,"allow_equal":{"type":"boolean"}}),
 obj({"kind":{"const":"unexplained_match"},"select":SELECT,"events":REL,
      "explained_by":{"type":"array","minItems":1,"maxItems":4,"items":REL}})
]
COMMON={"id":STR,"requires_complete_capture":{"type":"boolean"},
        "evidence":{"type":"array","minItems":1,"maxItems":8,"items":obj({"document_id":STR,"quote":{"type":"string","minLength":8,"maxLength":500}})},
        "normal_exceptions":{"type":"array","maxItems":4,"items":STR},
        "reason":{"type":"string","minLength":8,"maxLength":400}}
EXEC=obj({**COMMON,"role":{"enum":["HR_candidate","observed_SR_candidate"]},
          "plan":{"oneOf":PLANS},"needed":{"type":"array","maxItems":0,"items":STR}})
HOLD=obj({**COMMON,"role":{"const":"request_evidence"},"plan":{"type":"null"},
          "needed":{"type":"array","minItems":1,"maxItems":5,"items":STR}})
SCHEMA=obj({"candidates":{"type":"array","maxItems":4,"items":{"oneOf":[EXEC,HOLD]}},
            "no_candidate_reason":{"type":["string","null"],"maxLength":400}})
SCHEMA["$defs"]={"atom":ATOM}

SYSTEM="""Design justified logical checks from the supplied schema, documents and records.
Select the purpose, actual columns, joins, predicates, exceptions and HR/SR role yourself.
No correct rule, target operator or target condition is supplied. Return 0..4 concise candidates.
Use documented meaning, never rarity, sample identifiers, or a supplied percentile.
HR requires an explicit verified requirement/prohibition. A precisely described operational-review
diagnostic may be observed_SR_candidate when it is NOT an assertion of a forbidden business outcome.
Permitted behavior is not a violation. If the only documented fact permits the current pattern,
return no justified candidate, rather than inventing a stricter rule or unrelated evidence request.
Missing required semantic/capture evidence => request_evidence, plan=null, specific needed facts.
Executable candidates have needed=[]; it is NOT a source-column list or an applicability list.
Quote exact source text. These controlled documents are NOT real company policies.
Use the compact plan operators below. They are generic operations, not predefined business rules.
Every select is {table,filter:[atoms]}; atoms are ANDed. An atom is {column,op,value};
op eq/ne/lt/le/gt/ge/in/not_in/is_missing/is_present. in/not_in uses a list, missing/present uses null value.
For a relation join, source is a column of the current outer/event row, target is a column of the related row.
Multiple joins are ANDed. Never substitute a list of the sample IDs for a source relationship.
Plans:
row_check: select.filter IS the firing condition; every selected row fires, so an empty filter is not justified.
conditional_row_check: select.filter is ONLY the applicability condition, and violation is a nonempty
array of ANDed atoms on the same root row. Evaluate applicability FIRST: false gives false, unknown
gives unknown, and true then evaluates violation. Use when the source requires unresolved applicability
to remain unavailable even if a violation atom is false. Do not put the violation inside select.filter.
Independent sufficient violations need separate candidates; this operator does not add implicit OR.
duplicate_key: select,keys. Fires for repeated composite keys within the selected population. Same key components
in a different grouping context are not duplicates unless the whole scoped key is equal.
relation_check: select,related:{select,join},violation.
required_match_missing fires only when the required related match is absent;
match_present fires only when the specified forbidden/diagnostic related match EXISTS;
match_not_exactly_one fires when a required exact-one relationship does not have exactly one match.
dependency_order: select,link,parent_end,child_start,allow_equal. Link source identifies current row's predecessor;
target identifies the predecessor row in the SAME table. Fires only when predecessor completion is too late.
A null source link is not an active dependency. Use only with documented execution ordering and verified clocks/units.
unexplained_match: select,events:{select,join},explained_by:[{select,join}].
Fires when an outer row has a matching event for which NONE of the documented explanation relations exists.
Each explained_by join source is a column of that EVENT, target is a column of the explanation row.
Encode all documented normal handling/retry exceptions through those relations, not only in prose.
Use actual parent/context key joins for explanations too; a recovery in another transaction does not explain this event.
No arbitrary code/SQL. Null/unknown is never silently false. Require complete capture for absence-based conclusions.
Do not add unrelated missingness conditions to a key or relationship check. Missing input is unavailable, not a violation.
Before responding, compare the positive meaning of your selected operator against the cited contract and your own reason.
"""

def messages(case):
    public={k:deepcopy(case[k])for k in("schema","documents","capture","records","question")}
    public["physical_column_inventory"]=inventory(case)
    return [{"role":"system","content":SYSTEM},{"role":"user","content":json.dumps(public,separators=(",",":"))}]

def all3(values):
    return False if any(x is False for x in values)else None if any(x is None for x in values)else True
def any3(values):
    return True if any(x is True for x in values)else None if any(x is None for x in values)else False

def atom(row,a):
    v=row.get(a["column"]);op=a["op"];w=a["value"]
    if op=="is_missing":return v is None
    if op=="is_present":return v is not None
    if v is None:return None
    return {"eq":lambda:v==w,"ne":lambda:v!=w,"lt":lambda:v<w,"le":lambda:v<=w,
            "gt":lambda:v>w,"ge":lambda:v>=w,"in":lambda:v in w,"not_in":lambda:v not in w}[op]()
def filt(row,atoms):return all3([atom(row,a)for a in atoms])

class Invalid(ValueError):pass
def columns_of_select(select,cols,types,identifiers=None):
    table=select["table"]
    if table not in cols:raise Invalid("Unknown table "+table)
    for a in select["filter"]:
        if a["column"]not in cols[table]:raise Invalid("Unknown column "+table+"."+a["column"])
        op,v=a["op"],a["value"];typ=types[table,a["column"]]
        if op in("is_missing","is_present"):
            if v is not None:raise Invalid("Missingness operators require value=null")
        elif op in("in","not_in"):
            if not isinstance(v,list)or not v:raise Invalid("Membership requires nonempty value list")
        elif isinstance(v,list)or v is None:raise Invalid("Scalar comparison requires a known scalar")
        if op not in("is_missing","is_present"):
            if identifiers and(table,a["column"])in identifiers:raise Invalid("Sample row identifiers cannot be fixed filter values; bind source relationships")
            for value in(v if isinstance(v,list)else[v]):
                vtype="boolean"if isinstance(value,bool)else"number"if isinstance(value,(int,float))else"string"
                if typ not in(vtype,"unknown"):raise Invalid("Atom source/value type mismatch")
        if op in("lt","le","gt","ge")and(typ!="number"or isinstance(v,bool)or not isinstance(v,(int,float))):
            raise Invalid("Ordered atom requires numeric operands")
    return table

def validate_plan(case,plan):
    cols,types=inventory(case),source_types(case);identifiers=set()
    for table,decl in case["schema"].items():
        identifiers.update((table,k)for k in decl.get("keys",[]))
        for col,target in decl.items():
            if isinstance(target,str)and"."in target:
                identifiers.add((table,col));identifiers.add(tuple(target.split(".",1)))
    outer=columns_of_select(plan["select"],cols,types,identifiers)
    def relation(rel,src):
        dst=columns_of_select(rel["select"],cols,types,identifiers)
        for j in rel["join"]:
            if j["source"]not in cols[src]or j["target"]not in cols[dst]:raise Invalid("Join column absent from actual source")
            a,b=types[src,j["source"]],types[dst,j["target"]]
            if a!=b and"unknown"not in(a,b):raise Invalid("Join key type mismatch")
        return dst
    kind=plan["kind"]
    if kind=="row_check"and not plan["select"]["filter"]:raise Invalid("row_check cannot fire unconditionally")
    if kind=="conditional_row_check":
        if not plan["select"]["filter"]:raise Invalid("conditional_row_check needs an explicit applicability condition")
        if not plan["violation"]:raise Invalid("conditional_row_check needs a nonempty violation condition")
        columns_of_select({"table":outer,"filter":plan["violation"]},cols,types,identifiers)
    if kind=="duplicate_key":
        if any(k not in cols[outer]for k in plan["keys"]):raise Invalid("Duplicate key column missing")
        if len(set(plan["keys"]))!=len(plan["keys"]):raise Invalid("Repeated key component")
    if kind=="relation_check":relation(plan["related"],outer)
    if kind=="unexplained_match":
        child=relation(plan["events"],outer)
        for rel in plan["explained_by"]:relation(rel,child)
    if kind=="dependency_order":
        relation({"select":plan["select"],"join":plan["link"]},outer)
        a,b=plan["parent_end"],plan["child_start"]
        if a not in cols[outer]or b not in cols[outer]or types[outer,a]!="number"or types[outer,b]!="number":raise Invalid("Dependency times must be verified numeric sources")
        units=case["schema"].get(outer,{}).get("units",{})
        if not units.get(a)or units.get(a)!=units.get(b):raise Invalid("Verified shared time unit/clock required for dependency order")
        if not(str(units[a]).startswith("verified_same_clock_")or case["capture"].get("shared_clock_verified")is True):
            raise Invalid("Shared clock confirmation absent; equal unit names alone do not confirm a shared clock")
    return outer

def matches(case,source,rel):
    out=[];unknown=False
    for index,row in enumerate(case["records"][rel["select"]["table"]]):
        values=[None if source.get(j["source"])is None or row.get(j["target"])is None
                else source[j["source"]]==row[j["target"]]for j in rel["join"]]
        value=all3(values+[filt(row,rel["select"]["filter"])])
        if value is True:out.append((rel["select"]["table"],index,row))
        if value is None:unknown=True
    return out,unknown

def execute_plan(case,plan,requires_complete,matches_fn=None):
    match=matches_fn or (lambda source,rel:matches(case,source,rel))
    outer=plan["select"]["table"];complete=case["capture"].get("entity_family_complete")is True;rows=[]
    for index,row in enumerate(case["records"][outer]):
        applicable=filt(row,plan["select"]["filter"]);support=[];kind=plan["kind"]
        if applicable is not True:value=applicable
        elif requires_complete and not complete:value=None
        elif kind=="row_check":value=True
        elif kind=="conditional_row_check":value=filt(row,plan["violation"])
        elif kind=="duplicate_key":
            if any(row.get(k)is None for k in plan["keys"]):value=None
            else:
                identical=[i for i,r in enumerate(case["records"][outer])if filt(r,plan["select"]["filter"])is True and all(r.get(k)==row[k]for k in plan["keys"])]
                support=[(outer,i)for i in identical]
                uncertain=False
                for i,r in enumerate(case["records"][outer]):
                    if i==index:continue
                    possible=all3([filt(r,plan["select"]["filter"])]+[
                        None if r.get(k)is None else r[k]==row[k]for k in plan["keys"]])
                    uncertain|=possible is None
                value=True if len(identical)>1 else None if uncertain or not complete else False
        elif kind=="relation_check":
            found,unknown=match(row,plan["related"]);support=[(t,i)for t,i,r in found];mode=plan["violation"]
            if mode=="match_present":value=True if found else None if unknown or not complete else False
            elif mode=="required_match_missing":
                value=False if found else None if unknown or not complete else True
            elif mode=="match_not_exactly_one":
                value=True if len(found)>1 else None if unknown or not complete else len(found)!=1
            else:raise Invalid("Unknown relation violation")
        elif kind=="dependency_order":
            occurrence_keys=case["schema"].get(outer,{}).get("keys",[])
            if any(row.get(k)is None for k in occurrence_keys):value=None
            elif any(row.get(j["source"])is None for j in plan["link"]):value=False
            else:
                found,unknown=match(row,{"select":{"table":outer,"filter":[]},"join":plan["link"]})
                support=[(t,i)for t,i,r in found]
                if unknown or len(found)!=1 or row.get(plan["child_start"])is None:value=None
                else:
                    end=found[0][2].get(plan["parent_end"]);start=row[plan["child_start"]]
                    value=None if end is None else end>start if plan["allow_equal"]else end>=start
        elif kind=="unexplained_match":
            events,unknown=match(row,plan["events"]);unexplained=[]
            for t,i,event in events:
                support.append((t,i));explanations=[];uncertain=False
                for rel in plan["explained_by"]:
                    found,u=match(event,rel);explanations.extend(found);uncertain|=u
                support.extend((t,i)for t,i,r in explanations)
                unexplained.append(False if explanations else None if uncertain or not complete else True)
            value=any3(unexplained+([None]if unknown else[]))
            if not events and not complete:value=None
        else:raise Invalid("Unknown plan kind")
        rows.append({"source_table":outer,"source_row_index":index,"source_record":row,"value":value,
                     "support_rows":[{"table":t,"row_index":i}for t,i in sorted(set(support))]})
    return rows

def validate_and_execute(case,proposal):
    errors=[];results=[]
    try:jsonschema.Draft202012Validator(SCHEMA).validate(proposal)
    except (jsonschema.ValidationError,TypeError)as e:
        return {"executable_valid":False,"errors":[str(e)[:600]],"rules":[],"decision":"invalid","policy":POLICY}
    if not proposal["candidates"]and not proposal["no_candidate_reason"]:
        return {"executable_valid":False,"errors":["No-candidate outcome requires a specific reason"],"rules":[],"decision":"invalid","policy":POLICY}
    docs={d["id"]:d for d in case["documents"]};ids=set()
    for candidate in proposal["candidates"]:
        cid=candidate["id"];local=[]
        if cid in ids:local.append("Duplicate candidate id")
        ids.add(cid)
        for evidence in candidate["evidence"]:
            d=docs.get(evidence["document_id"])
            if not d or evidence["quote"]not in d["text"]:local.append("Evidence quote not exact source substring")
            if candidate["role"]=="HR_candidate"and(not d or d["authority"]not in("controlled_evaluation_contract_not_company_policy","reviewed_business_contract","verified_DDL")):
                local.append("HR requires verified contract authority, not a monitoring note")
        plan=candidate["plan"]
        if plan is not None:
            try:validate_plan(case,plan)
            except(Invalid,KeyError,TypeError)as e:local.append(str(e))
        if local:
            errors.extend({"candidate":cid,"error":x}for x in local);continue
        if plan is None:rows=[];decision="unavailable"
        else:
            rows=execute_plan(case,plan,candidate["requires_complete_capture"])
            value=any3([r["value"]for r in rows]);decision="warning"if value is True else"unavailable"if value is None else"nonwarning"
        results.append({"id":cid,"role":candidate["role"],"decision":decision,"rows":rows,
                        "semantic_review":"pending"})
    decision="invalid"if errors else"warning"if any(r["decision"]=="warning"for r in results)else"unavailable"if any(r["decision"]=="unavailable"for r in results)else"nonwarning"
    return {"executable_valid":not errors,"errors":errors,"rules":results,"decision":decision,
            "policy":POLICY,"semantic_entailment_proven_by_code":False,"operational_approval":False}

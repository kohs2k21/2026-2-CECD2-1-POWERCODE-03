"""Apply frozen generated plans to new same-schema logs. No LLM, fit or threshold update."""
from pathlib import Path
import json,argparse,csv
import jsonschema
from run_discovery import normalize
from specmodel.io import read,write,digest,sha
from logic_engine import inventory
import typed_plan as algebra
from discovery_runtime import execute_event,validate_plan,POLICY

def execute_pack(pack,raw,maximum_rows=2000,indexed=False):
    case=normalize(raw,maximum_rows=maximum_rows)
    if indexed:
        from indexed_executor import IndexedExecutor
        executor=IndexedExecutor(case)
    if pack["policy"]!=POLICY:raise ValueError("Unsupported execution policy; explicit migration required")
    if digest(case["schema"])!=pack["physical_schema_sha"]:raise ValueError("Schema changed; regenerate/review bindings before execution")
    if digest(case["documents"])!=pack["documents_sha"]:raise ValueError("Document/meaning evidence changed; review rules before execution")
    if inventory(case)!=pack["inferred_source_columns"]:raise ValueError("Physical column inventory changed")
    proposal=pack["proposal"]
    if pack.get("predicate_consistency_policy"):
        from predicate_consistency import errors as contradictions,SUPPORTED_POLICIES
        if pack["predicate_consistency_policy"]not in SUPPORTED_POLICIES:raise ValueError("Unsupported predicate consistency policy")
        if contradictions(proposal,policy=pack["predicate_consistency_policy"]):raise ValueError("Impossible logical predicate under the frozen validation policy")
    if pack.get("input_availability_policy"):
        from input_availability_gate import errors,POLICY as AVAILABILITY_POLICY
        if pack["input_availability_policy"]!=AVAILABILITY_POLICY:raise ValueError("Unsupported required-input availability policy")
        if errors(proposal):raise ValueError("Required-input availability contract failed")
    if digest({"proposal":proposal,"schema":case["schema"],"documents":case["documents"]})!=pack["definition_sha"]:
        raise ValueError("Rule definition/evidence integrity mismatch")
    jsonschema.Draft202012Validator(pack["generation_schema"]).validate(proposal)
    mismatches=[]
    for t,rows in case["records"].items():
        for i,row in enumerate(rows):
            for col,v in row.items():
                if v is None:continue
                actual="boolean"if isinstance(v,bool)else"number"if isinstance(v,(int,float))else"string"if isinstance(v,str)else"unsupported"
                expected=pack["inferred_source_types"].get(t+"."+col,"unknown")
                if expected not in(actual,"unknown"):
                    mismatches.append({"table":t,"row":i,"column":col,"expected":expected,"actual":actual})
    rules=[];features=[]
    for c in proposal["candidates"]:
        p=c["plan"];why=None
        if mismatches:rows=[];decision="unavailable";why="source_type_mismatch"
        elif p is None:rows=[];decision="unavailable";why="needed_evidence_missing"
        elif not pack["execution"]["executable_valid"]:
            rows=[];decision="unavailable";why="source_rule_bundle_not_verified"
        else:
            try:validate_plan(case,p)
            except (algebra.Invalid,KeyError,TypeError)as e:
                rows=[];decision="unavailable";why="current_source_plan_validation_failed: "+str(e)
            else:
                if p["kind"]=="dependency_order"and case["capture"].get("shared_clock_verified")is False:
                    rows=[];decision="unavailable";why="shared_clock_not_confirmed"
                else:
                    rows=executor.execute(p,c["requires_complete_capture"])if indexed else execute_event(case,p,c["requires_complete_capture"])if p["kind"]=="unexplained_event"else algebra.execute_plan(case,p,c["requires_complete_capture"])
                    value=algebra.any3([r["value"]for r in rows]);decision="warning"if value is True else"unavailable"if value is None else"nonwarning"
                    if value is None:why="incomplete_capture"if c["requires_complete_capture"]and not case["capture"]["entity_family_complete"]else"unresolved_relation_or_source_value"
        entry={"rule_id":c["id"],"role":c["role"],"decision":decision,"unavailable_reason":why,
               "rows":rows,"evidence":c["evidence"],"definition_sha":pack["definition_sha"]}
        rules.append(entry)
        for row in rows:
            keys=case["schema"].get(row["source_table"],{}).get("keys",[])
            features.append({"feature_id":"rule_predicate:"+c["id"],"source_table":row["source_table"],
                "source_row_index":row["source_row_index"],"source_keys":{k:row["source_record"].get(k)for k in keys},
                "value":row["value"],"state":"unavailable"if row["value"]is None else"value",
                "unit":"boolean","definition_sha":pack["definition_sha"],"source_version":case["capture"]["source_version"]})
    decision="warning"if any(r["decision"]=="warning"for r in rules)else"unavailable"if any(r["decision"]=="unavailable"for r in rules)else"nonwarning"
    return {"policy":POLICY,"decision":decision,"rules":rules,"logical_features":features,"type_mismatches":mismatches,
            "source_version":case["capture"]["source_version"],"source_sha":digest(case),
            "actual_LLM_calls":0,"model_or_threshold_fit":False,"frozen_rule_definition_sha":pack["definition_sha"],
            "indexed_batch_execution":indexed,"LLM_input_row_limit_bypassed":False,
            "operational_approval":False,"scope":"research execution; candidate review remains required"}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rules",required=True,type=Path);p.add_argument("--input",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    write(a.output/"input_pins.json",{"rule_pack_path":str(a.rules.resolve()),"rule_pack_sha256":sha(a.rules),
          "new_input_path":str(a.input.resolve()),"new_input_sha256":sha(a.input)})
    try:
        result=execute_pack(read(a.rules),read(a.input));write(a.output/"decisions.json",result)
        with(a.output/"logical_features.csv").open("w",newline="")as f:
            keys=["feature_id","source_table","source_row_index","source_keys","value","state","unit","definition_sha","source_version"]
            writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader()
            for row in result["logical_features"]:
                writer.writerow({**row,"source_keys":json.dumps(row["source_keys"],sort_keys=True),
                    "value":""if row["value"]is None else int(row["value"])})
        print(json.dumps({"decision":result["decision"],"rules":len(result["rules"]),"actual_LLM_calls":0}))
    except Exception as e:
        write(a.output/"rejected.json",{"decision":"unavailable","reason":str(e),"actual_LLM_calls":0,"silent_schema_reuse":False})
        raise
if __name__=="__main__":main()

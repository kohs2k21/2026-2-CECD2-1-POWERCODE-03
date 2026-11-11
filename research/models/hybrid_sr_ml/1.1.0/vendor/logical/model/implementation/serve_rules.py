"""Research serving envelope for frozen logical plans; never treats no rules as normal."""
from copy import deepcopy
from pathlib import Path
import argparse,json
from apply_rules import execute_pack
from run_discovery import normalize
from typed_plan import matches,any3,POLICY as TYPED_RUNTIME_POLICY
from specmodel.io import read,write,sha,digest
SERVING_POLICY="logical-rule-serving1.5-evidence-dominance"

def serve(pack,raw,bulk=False):
    result=execute_pack(pack,raw,maximum_rows=None if bulk else 2000,indexed=bulk)
    result["serving_policy"]=SERVING_POLICY
    result["typed_operator_runtime_policy"]=TYPED_RUNTIME_POLICY
    case=normalize(raw,maximum_rows=None if bulk else 2000)
    if bulk:
        from indexed_executor import IndexedExecutor
        indexes=IndexedExecutor(case)
    executable=[x for x in pack["proposal"]["candidates"] if x["plan"] is not None]
    by_id={x["id"]:x for x in executable}
    result["unobserved_executable_rule_ids"]=[]
    for rule in result["rules"]:
        if rule["rule_id"] in by_id and not rule["rows"] and rule["decision"]=="nonwarning":
            rule["decision"]="unavailable"
            rule["unavailable_reason"]="no_source_rows_observed_for_this_rule"
            result["unobserved_executable_rule_ids"].append(rule["rule_id"])
    if result["unobserved_executable_rule_ids"] and result["decision"]!="warning":
        result["decision"]="unavailable"
        result["unavailable_reason"]="No source rows were observed for some executable checks."
    if not executable:
        result["decision"]="unavailable"
        result["coverage_status"]="no_executable_rule"
        result["unavailable_reason"]="no justified executable rule was generated; this is not a normality decision"
    elif result["unobserved_executable_rule_ids"]:result["coverage_status"]="candidate_rules_without_source_observations"
    else:result["coverage_status"]="candidate_rules_evaluated"
    from export_contracts import export,bind_values
    contracts=export(pack,case)
    result["linked_feature_contracts"]=contracts["feature_contracts"]
    result["linked_rule_recommendations"]=contracts["rule_recommendations"]
    result["linked_feature_values"]=bind_values(contracts,result["logical_features"])
    result["evidence_requests"]=contracts["evidence_requests"]
    result["pending_evidence_count"]=len(result["evidence_requests"])
    result["coverage_complete_for_proposed_checks"]=(bool(executable) and not result["evidence_requests"]
        and not result["unobserved_executable_rule_ids"]
        and all(r["decision"] in ("warning","nonwarning") for r in result["rules"]))
    if executable and result["evidence_requests"]:
        result["coverage_status"]="candidate_rules_evaluated_with_pending_evidence"
        if result["decision"]!="warning":
            result["decision"]="unavailable"
            result["unavailable_reason"]="Some proposed checks require unresolved evidence; evaluated nonwarnings do not complete coverage."
    groups={}
    for r in result["rules"]:
        if r["rule_id"] not in by_id:continue
        p=by_id[r["rule_id"]]["plan"]
        if p["kind"]!="unexplained_event":continue
        rel=deepcopy(p["context"]);rel["select"]["filter"]=[]
        for row in r["rows"]:
            found,unknown=indexes.matches(row["source_record"],rel)if bulk else matches(case,row["source_record"],rel)
            if unknown or len(found)!=1:continue
            table,index,context=found[0]
            keys=list(dict.fromkeys(j["target"] for j in rel["join"]))
            identity={k:context.get(k) for k in keys}
            group=groups.setdefault((table,digest(identity)),{"context_table":table,"context_keys":identity,
                "context_row_index":index,"rule_ids":[],"event_evidence":[],"values":[]})
            if r["rule_id"] not in group["rule_ids"]:group["rule_ids"].append(r["rule_id"])
            group["event_evidence"].append({"rule_id":r["rule_id"],"source_table":row["source_table"],
                "source_row_index":row["source_row_index"],"source_record":row["source_record"],
                "value":row["value"],"support_rows":row["support_rows"]})
            group["values"].append(row["value"])
    result["context_decisions"]=[]
    for group in groups.values():
        value=any3(group.pop("values"));group["value"]=value
        group["decision"]="warning" if value is True else "unavailable" if value is None else "nonwarning"
        result["context_decisions"].append(group)
    result["coverage_caution"]="No finding means only these candidate predicates did not fire; pending evidence preserves unavailable. It is not proof of business normality."
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--rules",required=True,type=Path)
    p.add_argument("--input",required=True,type=Path);p.add_argument("--output",required=True,type=Path)
    p.add_argument("--bulk",action="store_true",help="Full-batch indexed code execution; never sends these rows to an LLM")
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    write(a.output/"input_pins.json",{"rule_pack_sha256":sha(a.rules),"new_input_sha256":sha(a.input),
          "serving_policy":SERVING_POLICY,"actual_LLM_calls":0})
    try:
        result=serve(read(a.rules),read(a.input),bulk=a.bulk);write(a.output/"result.json",result)
        print(json.dumps({"decision":result["decision"],"coverage_status":result["coverage_status"],
            "rules":len(result["rules"]),"context_decisions":len(result["context_decisions"]),"actual_LLM_calls":0}))
    except Exception as e:
        write(a.output/"result.json",{"decision":"unavailable","coverage_status":"rejected",
            "reason":str(e),"actual_LLM_calls":0,"serving_policy":SERVING_POLICY})
        raise
if __name__=="__main__":main()

"""Source-bound reusable-rule actual local generation; no labels/gold loaded."""
from pathlib import Path
import sys,json,gc,time,argparse,shutil,traceback
from run import PROJECT,PACKAGE,BASE,DESIGN
from specmodel.io import read,write,sha,digest,Budget
from specmodel.local_models import LocalModels
from recommend import admit
from discovery_runtime import messages,schema_for,validate_and_execute,POLICY,CITATION_POLICY
from logic_engine import inventory,source_types
ROOT=Path(__file__).resolve().parents[1]
REPAIR="""Correct the contract/source/type errors below using only the supplied documents.
Do not invent a missing rule or change the documented purpose. Bind actual source columns
with joins, never sample-ID constants. For unexplained_event, select is the event being
reviewed; context and all explained_by joins start from that event's columns.
No expected condition is provided. Keep a safe evidence request when necessary.
"""
def normalize(case,maximum_rows=2000):
    from copy import deepcopy
    case=deepcopy(case);capture=case["capture"]
    for target,legacy in[("Body_included","body_included"),("source_version","source_version")]:
        if legacy in case:
            if target in capture and capture[target]!=case[legacy]:
                raise ValueError("Conflicting top-level/capture metadata: "+target)
            capture.setdefault(target,case[legacy])
    return admit(case,maximum_rows=maximum_rows)
def main():
    p=argparse.ArgumentParser();p.add_argument("--suite",choices=["development","heldout"],default="development")
    p.add_argument("--input",type=Path);p.add_argument("--input-dir",type=Path);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--arm",choices=["Base","RuleLoRA","Candidate"],default="Base")
    p.add_argument("--candidate",type=Path);p.add_argument("--repair-limit",type=int,choices=[0,1],default=1)
    p.add_argument("--source-review",action="store_true",help="One additional source-only semantic critique; no evaluator feedback")
    p.add_argument("--execution-feedback",action="store_true",help="Include exact predicate meaning and measured source rows in source review")
    p.add_argument("--strict-input-guards",action="store_true",help="Reject population filters that conceal missing required join/group keys")
    a=p.parse_args()
    if a.input and a.input_dir:raise ValueError("Choose a single input or a directory, not both")
    if a.execution_feedback and not a.source_review:raise ValueError("--execution-feedback requires --source-review")
    if a.arm=="Candidate"and not a.candidate:raise ValueError("Explicit candidate adapter required")
    files=[a.input]if a.input else list(sorted(a.input_dir.glob("*.json")))if a.input_dir else list(sorted((ROOT/"heldout_pilot/inputs").glob("*.json")))if a.suite=="heldout"else list(sorted((DESIGN/"evaluation_inputs").glob("*.json")))+list(sorted((ROOT/"observational_review_trial/inputs").glob("*.json")))
    if not files:raise ValueError("No admitted input files")
    # Fail before loading model weights if a directory includes sidecar JSON.
    # Never silently drop an input, and never interpret provenance as log data.
    admitted = {}
    for source_path in files:
        try:
            admitted[str(source_path)] = normalize(read(source_path))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("Input admission failed before model load: " + str(source_path) + ": " + str(exc)) from exc
    a.output.mkdir(parents=True,exist_ok=False);out=a.output
    for name in["run_discovery.py","discovery_runtime.py","typed_plan.py","logic_engine.py","recommend.py","source_review_prompt.py","execution_feedback.py","input_availability_gate.py","citation_spans.py","predicate_consistency.py","plan_satisfiability.py","declared_reference_guard.py"]:
        source=Path(__file__).with_name(name);target=out/"source_snapshot"/name
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    max_calls=len(files)*(1+a.repair_limit+int(a.source_review))
    write(out/"protocol.json",{"policy":POLICY,"arm":a.arm,"suite":a.suite,"max_calls":max_calls,
          "repair_limit":a.repair_limit,"only_code_feedback_on_repair":True,
          "gold_not_loaded":True,"SFT_run":False,"common_reusable_task_purpose":True,
          "source_bound_syntax_not_answer_constraints":True,"citation_policy":CITATION_POLICY,
          "source_only_semantic_review":a.source_review,"evaluator_decisions_not_supplied":True,
          "source_execution_feedback":a.execution_feedback,
          "input_availability_guard":a.strict_input_guards,
          "literal_predicate_consistency_guard":a.strict_input_guards,
          "candidate_sha":sha(a.candidate/"adapter_model.safetensors")if a.candidate else None,
          "inputs":[{"path":str(q),"sha256":sha(q)}for q in files]})
    write(out/"selected_models.json",read(PACKAGE/"model_manifest.json"))
    model=LocalModels(BASE,candidate_feature=a.candidate);budget=Budget(out,max_calls,phase="source_bound_reusable_"+a.suite)
    items=[];start=time.monotonic()
    try:
        for q in files:
            case=admitted[str(q)];dest=out/q.stem;dest.mkdir()
            msgs=messages(case);schema=schema_for(case);write(dest/"source.json",case)
            if a.source_review:
                from source_review_prompt import SYSTEM,REVIEW,PROMPT_POLICY
                msgs[0]["content"]=SYSTEM
            attempts=[]
            def validate(proposal):
                result=validate_and_execute(case,proposal)
                if a.strict_input_guards:
                    from input_availability_gate import apply
                    result=apply(result,proposal)
                    from predicate_consistency import apply as consistent
                    result=consistent(result,proposal)
                    if result["executable_valid"]:
                        from declared_reference_guard import errors as reference_errors, POLICY as REFERENCE_POLICY
                        bad=reference_errors(case,proposal)
                        result={**result,"declared_reference_guard_policy":REFERENCE_POLICY}
                        if bad:
                            result={**result,"executable_valid":False,"decision":"invalid","errors":bad,"rules":[]}
                return result
            for attempt in range(1+a.repair_limit):
                d=dest/("first"if attempt==0 else"code_repair");d.mkdir()
                r=model.generate("rule"if a.arm=="RuleLoRA"else"feature",msgs,schema,d,budget,
                     q.stem+"_attempt"+str(attempt),maximum=2048,arm="candidate"if a.candidate else"Base")
                check=validate(r["parsed"]);write(d/"execution.json",check)
                attempts.append({"attempt":attempt,"source_valid":check["executable_valid"],"decision":check["decision"],
                                 "errors":check["errors"],"seconds":r["seconds"]})
                if check["executable_valid"]or attempt==a.repair_limit:break
                msgs+=[{"role":"assistant","content":r["raw"]},
                       {"role":"user","content":REPAIR+"\nErrors: "+json.dumps(check["errors"])}]
            if a.source_review and check["executable_valid"]:
                review_text=REVIEW
                if a.execution_feedback:
                    from execution_feedback import feedback
                    facts=feedback(r["parsed"],check);write(dest/"compiler_execution_feedback.json",facts)
                    review_text+="\nCompiler facts from supplied source rows only:\n"+json.dumps(facts,separators=(",",":"))
                msgs+=[{"role":"assistant","content":r["raw"]},{"role":"user","content":review_text}]
                d=dest/"source_review";d.mkdir()
                r=model.generate("rule"if a.arm=="RuleLoRA"else"feature",msgs,schema,d,budget,
                     q.stem+"_source_review",maximum=2048,arm="candidate"if a.candidate else"Base")
                check=validate(r["parsed"]);write(d/"execution.json",check)
                attempts.append({"attempt":"source_only_semantic_review","source_valid":check["executable_valid"],
                                 "decision":check["decision"],"errors":check["errors"],"seconds":r["seconds"]})
            write(dest/"attempts.json",attempts)
            pack={"policy":POLICY,"proposal":r["parsed"],"execution":check,
                  "definition_sha":digest({"proposal":r["parsed"],"schema":case["schema"],"documents":case["documents"]}),
                  "physical_schema_sha":digest(case["schema"]),"documents_sha":digest(case["documents"]),
                  "source_version":case["capture"]["source_version"],"source_file_sha256":sha(q),
                  "generation_schema":schema,"inferred_source_columns":inventory(case),
                  "inferred_source_types":{t+"."+c:v for(t,c),v in source_types(case).items()},
                  "adopted_operational_rules":[],"semantic_and_operational_review":"pending",
                  "actual_model_weight_sha":r["model"]["adapter_weight_sha"],
                  "Base_revision":r["model"]["Base"]["revision"]}
            pack["prompt_policy"]=PROMPT_POLICY if a.source_review else "source-bound-original1.0"
            pack["citation_policy"]=CITATION_POLICY
            if a.execution_feedback:pack["execution_feedback_policy"]="source-only-execution-feedback1.0"
            if a.strict_input_guards:
                pack["input_availability_policy"]="required-input-availability1.0"
                from predicate_consistency import POLICY as CONSISTENCY_POLICY
                pack["predicate_consistency_policy"]=CONSISTENCY_POLICY
                from declared_reference_guard import POLICY as REFERENCE_POLICY
                pack["declared_reference_guard_policy"]=REFERENCE_POLICY
            write(dest/"candidate_pack.json",pack)
            item={"case":q.stem,"decision":check["decision"],"source_valid":check["executable_valid"],
                  "roles":[x["role"]for x in check["rules"]],"attempts":len(attempts),
                  "errors":check["errors"],"tokens":r["output_tokens"]}
            items.append(item);write(out/"progress.json",items);print(json.dumps(item),flush=True)
    finally:
        write(out/"summary.json",{"items":items,"seconds":time.monotonic()-start,
              "actual_calls":len((out/"model_calls.jsonl").read_text().splitlines())if(out/"model_calls.jsonl").exists()else 0,
              "weights_updated":False,"semantic_accuracy_not_asserted_by_code":True})
        model.model=None;gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:pass
if __name__=="__main__":main()

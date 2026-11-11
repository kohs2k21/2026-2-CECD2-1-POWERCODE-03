"""Compiler-authored predicate meaning and measured firings; no answer labels."""
from copy import deepcopy
POLICY="source-only-execution-feedback1.0"

def describe_plan(p):
    root=p["select"]["table"];s={"root_table":root,"root_filters_AND":p["select"]["filter"],"kind":p["kind"]}
    if p["kind"]=="row_check":
        s.update(firing_condition="The root filters themselves are the complete firing condition.",
            cross_row_comparison_performed=False,related_record_comparison_performed=False)
    elif p["kind"]=="conditional_row_check":
        s.update(applicability_AND=p["select"]["filter"],violation_AND=p["violation"],
            firing_condition="Applicability false gives false; unknown gives unknown; true evaluates the ANDed violation atoms on the same row.",
            cross_row_comparison_performed=False,related_record_comparison_performed=False)
    elif p["kind"]=="relation_check":
        s.update(related=p["related"],firing_condition={"match_present":"One or more related rows satisfies ALL joins AND target filters.",
            "required_match_missing":"No related row satisfies ALL joins AND target filters, with complete/certain evidence.",
            "match_not_exactly_one":"The number of related rows satisfying ALL joins AND target filters is not exactly one."}[p["violation"]])
    elif p["kind"]=="unexplained_event":
        s.update(context=p["context"],explanation_alternatives_OR=p["explained_by"],
            firing_condition="The event passes its root filter; its unique context passes the context filter; NO explanation relation has a matching row. All join sources refer to the ROOT EVENT.")
    elif p["kind"]=="duplicate_key":s.update(group_key=p["keys"],firing_condition="More than one selected row has the same full group key.")
    elif p["kind"]=="dependency_order":s.update(link=p["link"],predecessor_end_column=p["parent_end"],current_start_column=p["child_start"],
        firing_condition="predecessor.end > current.start" if p["allow_equal"] else "predecessor.end >= current.start")
    return s

def feedback(proposal,execution):
    results={r["id"]:r for r in execution["rules"]};items=[]
    for c in proposal["candidates"]:
        if c["plan"] is None:
            items.append({"candidate_id":c["id"],"actual_execution":"not executable; evidence request","needed":c["needed"]});continue
        r=results.get(c["id"],{});rows=r.get("rows",[])
        counts={"fired":sum(x["value"] is True for x in rows),"not_fired":sum(x["value"] is False for x in rows),"unavailable":sum(x["value"] is None for x in rows)}
        examples=[]
        for value in(True,False,None):
            examples.extend(deepcopy([x for x in rows if x["value"] is value][:2]))
        items.append({"candidate_id":c["id"],"compiler_predicate_meaning":describe_plan(c["plan"]),
            "actual_measured_row_counts":counts,"representative_evaluated_rows":examples,
            "decision":r.get("decision","not_executed"),"semantic_entailment_not_proven":True})
    return {"policy":POLICY,"author":"deterministic execution code","teacher_answers_included":False,
            "gold_or_private_probes_used":False,"items":items,
            "review_task":"Compare the actual predicate meaning and observed firing rows with your claimed purpose and source document. A related row existing is not a mismatch test; non-null identifiers are not an equality/mismatch comparison. Correct contradictions or request the unsupported operation/evidence. Preserve source-grounded useful review hypotheses without inventing a business prohibition."}

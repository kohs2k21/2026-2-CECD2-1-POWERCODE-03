"""Sound, deliberately incomplete proofs that a logical plan cannot fire true.

This diagnoses operator logic only. It never supplies a business condition or
claims that a plan not rejected here is semantically justified by its source.
"""
import json

POLICY = "bounded-plan-satisfiability1.0"


def _members(values):
    return {json.dumps(value, sort_keys=True, separators=(",", ":")) for value in values}


def _implies_presence(context, explanation):
    if context["select"]["table"] != explanation["select"]["table"]:
        return False
    return (_members(explanation["join"]).issubset(_members(context["join"]))
            and _members(explanation["select"]["filter"]).issubset(
                _members(context["select"]["filter"])))


def proofs(plan):
    if plan is None:
        return []
    if plan["kind"] == "unexplained_event":
        for index, explanation in enumerate(plan["explained_by"]):
            if _implies_presence(plan["context"], explanation):
                return [{"proof": "qualifying_context_implies_explanation_presence",
                    "explanation_index": index,
                    "meaning": "Any row satisfying the required context necessarily has an explanation from that same context row. This plan cannot evaluate true; unresolved context may still yield unknown."}]
    if plan["kind"] == "relation_check" and plan["violation"] == "required_match_missing":
        relation = plan["related"]
        if (plan["select"]["table"] == relation["select"]["table"]
                and all(join["source"] == join["target"] for join in relation["join"])
                and _members(relation["select"]["filter"]).issubset(
                    _members(plan["select"]["filter"]))):
            return [{"proof": "selected_row_is_its_own_required_match",
                "meaning": "Every selected row with known join values matches itself in the same snapshot. Missing join values yield unknown rather than an absence finding; the absence predicate cannot evaluate true."}]
    return []


def errors(proposal):
    return [{"candidate": candidate["id"], "error": proof["meaning"],
             "proof": proof["proof"], "policy": POLICY}
            for candidate in proposal["candidates"] for proof in proofs(candidate["plan"])]

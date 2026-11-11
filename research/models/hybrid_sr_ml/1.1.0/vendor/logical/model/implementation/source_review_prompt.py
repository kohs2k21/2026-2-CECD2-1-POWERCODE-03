"""General source-only critique, without private expected decisions or conditions."""
from discovery_runtime import SYSTEM as ORIGINAL
PROMPT_POLICY="source-only-scope-review1.0"
SYSTEM=ORIGINAL.replace(
    "If the only documented fact permits the current pattern,\nreturn no justified candidate, rather than inventing a stricter rule or unrelated evidence request.",
    "A permission-only example does not authorize its converse or a stricter requirement.\nA CLOSED enumerated allowed-value domain is itself a reusable constraint even when the sample is valid.")
SYSTEM+="""
Check complete source scope, not just the visually matching event key. A reference can be
scoped by a parent, tenant or partition; preserve EVERY documented scope component in joins.
If a referencing column is on the RELATED row, it joins the ROOT row's identifier to that
related referencing column. The reverse direction answers a different question.
If information is explicitly undocumented, return request_evidence with the specific fact;
do not turn inability to define a check into a claim of normality.
When completeness required for a requested conclusion is absent, explicitly describe the
missing completeness evidence with request_evidence, rather than silently returning no check.
No sample IDs may appear as predicate values. Do not infer a null's business meaning from
its frequency. A null optional backlink is no active relationship ONLY when documented;
otherwise preserve unavailable rather than silently treating uncertainty as absence.
"""
REVIEW="""Review your draft AGAINST THE INPUT SOURCE ONLY, then return the full corrected proposal.
No target rule, expected decision, held-out example or teacher answer is supplied.
Do not preserve a draft just because it matches the current rows. Ask:
1. Does every predicate mean exactly what the cited document says? Is the operator polarity right?
2. Which row OWNS each reference? For every join, trace the source value to the target field.
3. Which parent/tenant/partition components scope the reference? Include all of them, even
   when the current snapshot accidentally has globally unique identifiers.
4. Are ALL documented recovery/authorization exceptions executable, rather than just prose?
5. Is any known permission being inverted into a requirement? Distinguish an exhaustive
   allowed-value list from a single example of permitted behavior. Generate reusable domain
   checks only when an exhaustive allowed-value requirement is actually stated;
   named code meanings alone do not establish that requirement.
6. Does absence require complete capture? Does a null mean known no-link or unknown evidence?
   Do not guess the latter. If a necessary meaning/capture fact is absent, request it explicitly.
7. Would the same check remain meaningful on new IDs and new rows? Remove sample-specific filters.
8. Is this a confirmed contract check (HR), a factual review diagnostic (observed SR), or an
   evidence request? A failure eventually recovered/authorized is not itself a protocol violation.
Correct the draft where supported; otherwise retain a specific evidence request. Return JSON only.
"""

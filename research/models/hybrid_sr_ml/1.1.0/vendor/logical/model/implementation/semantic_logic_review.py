"""Task-independent source review of logical connectives and supplied evidence.

No candidate IDs, field names, target values or private evaluation outcomes appear
here. This is an instruction for the model, not a code rewrite of its predicates.
"""
POLICY="source-logical-connectives-and-evidence-review1.4-source-evidence"
PROMPT_POLICY="source-only-scope-review1.5-source-evidence"
INSTRUCTIONS="""
Logical completeness review:
Every select.filter array is a CONJUNCTION: every atom must hold for a row to fire.
Separately required facts are not necessarily conjunctive violations. If a document
requires BOTH A and B, violation is NOT A OR NOT B, not NOT A AND NOT B. The current
filter language has no OR within one array: express independently sufficient
violations as separate candidates, whose alerts are combined by OR. Do not silently
weaken a requirement merely to fit it into one candidate. The same De Morgan logic
applies to permitted alternatives and to explanation relations.
For each multi-atom filter, mentally vary one fact while retaining the other facts:
does the calculated firing condition still represent the cited meaning? A current
all-valid sample cannot establish that a logically incomplete detector is correct.
Keep only conditions actually supported by the supplied source. Do not invent an
obligation merely because its violation would be representable.
Read the supplied capture metadata before requesting capture evidence. Do not ask
again for the identical fact already explicitly provided. Capture completeness is
evidence about observation coverage, not an assertion that a business obligation
exists. Distinguish missing business meaning from missing source coverage.
Do not require complete-family capture for every kind of check by default. A
documented NOT-NULL obligation is different from an unavailable input to another
calculation: a known null in an observed row can itself violate that explicit
obligation. A known invalid code or observed duplicate can likewise be positive
local evidence without seeing all other events. Preserve unknown inputs and the
executor's uncertainty about unseen related records; require complete capture
when a conclusion actually depends on absence or full-family observation.
When a coverage pass finds no additional supported check, return zero candidates
with an accurate no_candidate_reason; do not manufacture an evidence request just
to keep the candidates list nonempty.
An unknown applicability condition is different from an observed, permitted state.
When the source says that applicability must remain unresolved, use conditional_row_check
to separate applicability in select.filter from violation atoms. A single conjunction
can incorrectly hide unknown applicability behind a false violation atom. Do not invent
an applicability requirement that the source does not state.
For required unknown units, code meanings or relationship semantics explicitly identified
in the supplied documents, return a specific structured request_evidence object rather
than only a no_candidate_reason. A permission-only document with no such requested
missing fact may legitimately yield no additional candidate.
Review every supplied document for relevant permitted recovery and authorization
alternatives. A missing explanation in the observed sample is not permission to omit
that documented alternative from a reusable check. Cite the smallest sufficient source
spans; never replace an unsupported explanation with an invented successful sibling.

Source-purpose and quantifier review:
A dictionary explaining several code values is NOT an exhaustive allowed-value
contract. Do not infer that every unlisted value is forbidden. When the purpose
is to review reported outcomes, use the code whose documented meaning is the
outcome being reviewed; do not substitute an unrelated domain-membership check
or a check that ordinary keys are present. Presence itself is anomalous only
when the supplied source says so.
A reference naming a target supports a missing-target lookup in its documented
direction. It does not imply that every target must have a referring source.
The existence of a reference does not imply exactly one matching target:
match_not_exactly_one requires a supported exact-cardinality requirement.
Explicitly permitted multiple target copies must not become duplicate failures.
For a cross-record outcome diagnostic, preserve BOTH the documented root
outcome condition and the related outcome condition, and join every stated
scope key. An unsuccessful child alone is a different diagnostic from its
coexistence with a reported successful parent.
For observed duplicate or local-invalid-value diagnostics, observed positive
evidence remains positive even when further rows might be missing. Do not use
requires_complete_capture to suppress already observed positive local evidence;
the relation executor separately preserves unknown missing-target conclusions.
A normal_exceptions entry must agree with the executable plan. Do not claim a
recovery exception is implemented when no supplied relation expresses it.
Review against all documents and the requested purpose, not against a reusable
training-template phrase. If the source supports only a descriptive diagnostic,
do not call it a confirmed exact constraint or a verified business violation.
"""

INSTRUCTIONS += """
Evidence scope and complete enumeration:
requires_complete_capture is an ADDITIONAL whole-snapshot admission precondition,
not a description of the supplied snapshot. Use false unless an explicit source
requirement makes complete capture a prerequisite for the entire check. The generic
executor itself keeps absence-dependent conclusions unknown on incomplete data.
Do not set this flag true merely because the current sample is marked complete.
Before emitting the final JSON, review each separately stated required or prohibited
fact in every document, including non-English documents. Cover every independently
sufficient violation supported by that source, within the candidate budget. Do not
return just one field from a list of independently required fields and call the
source review complete. If the output budget cannot cover all justified checks, say
which coverage is incomplete; never change a conjunction to hide an omitted check.
For dependency_order, link.source belongs to the CURRENT dependent row and
link.target to its PREDECESSOR row. A declared current pointer to a predecessor
identity must be compared in that direction, not reversed. Matching scope columns
remain equality joins and do not reverse the dependency pointer.
"""

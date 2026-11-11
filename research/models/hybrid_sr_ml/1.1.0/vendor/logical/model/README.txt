LOCAL LOGICAL FEATURE AND RULE SPECIFICATION

This research bundle contains a real targeted QLoRA adapter, source-grounded local
generation, typed validation, frozen rule serving and linked feature/rule exports.
It adds non-statistical logical checks to the existing numerical Feature/Rule/ML
work. Existing Feature-51, Rule-527, IF, K-means and LOF files are not replaced.
Read selection_evidence.json for measured results and remaining limits.

Useful example
The included fictional source describes completed work, failed activities,
successful replay and authorized waiver in separate tables. The LLM generated a
review predicate for a failed activity of a successfully completed work unit when
neither correctly scoped handling record exists. The joins include site, work
and activity identity; another work unit's recovery does not explain this event.
This is a factual review candidate, not an assertion of a confirmed incident.

Prerequisites
Use the existing project Python environment inside the current Docker container.
No install, download, fit, threshold recalibration or model fallback runs here.
Generation additionally needs the pinned local Base weights and an exposed GPU.
Only the small logical adapter is bundled. Runtime dependency versions are recorded
in requirements.lock.txt; this records direct runtime pins, not a complete portable
transitive environment lock. The tested container environment is the reference.

For the commands below, run from this bundle directory and set:
  PYTHON='/home/msko20702/#종합설계/data/.venv/bin/python'
  SPEC_BASE_PATH='/path/to/pinned/Qwen3.6-27B/snapshot'
  ASSIGNED_GPU='0'  # example: select ONE GPU already assigned and exposed here
Every output path must be NEW. Original source/model/result files are preserved.

1. Verify the bundled files and selected model (CPU)
  "$PYTHON" -B cli.py check

2. Reproduce the saved actual-model example without another LLM call (CPU)
  "$PYTHON" -B verify_examples.py --output /path/to/new/example_check

3. Generate again with the selected adapter (one admitted source snapshot)
  CUDA_VISIBLE_DEVICES="$ASSIGNED_GPU" "$PYTHON" -B cli.py recommend \
    --input examples/source.json --base-path "$SPEC_BASE_PATH" \
    --output /path/to/new/recommendation
Use only a GPU exposed to this container. source.json is the generation input;
expected_results.json is not sent to the model. Candidate packs and raw attempts
remain available even if a generation fails. Source validity is not approval.

4. Apply the frozen generated rule to another same-schema batch (CPU; no LLM)
  "$PYTHON" -B cli.py serve \
    --rules /path/to/new/recommendation/source/candidate_pack.json \
    --input examples/handled_batch.json --output /path/to/new/served_batch
The case output directory is the input filename stem (source in this example).
For full-batch indexed execution add --bulk; full rows are never sent to the LLM.

5. Export a variable definition and a rule that references it (CPU)
  "$PYTHON" -B cli.py export \
    --rules /path/to/new/recommendation/source/candidate_pack.json \
    --source examples/source.json --output /path/to/new/linked_contracts
Served linked_feature_values use the same feature ID and definition SHA as these
contracts. Null stays unknown. Exports remain disabled pending review.

6. Accumulate more than one call's proposals when the source supports more checks
  CUDA_VISIBLE_DEVICES="$ASSIGNED_GPU" "$PYTHON" -B cli.py discover \
    --input /path/to/metadata_source.json --base-path "$SPEC_BASE_PATH" \
    --maximum-rounds 8 --output /path/to/new/discovery_library
No target rule count is imposed. Zero/new duplicates/holds or the round budget
stop exploration. Exhaustive discovery is not guaranteed. All prior proposals
remain UNAPPROVED and are never promoted to source evidence or teacher labels.

6a. Consider each source-table population systematically
  CUDA_VISIBLE_DEVICES="$ASSIGNED_GPU" "$PYTHON" -B cli.py discover-roots \
    --input /path/to/metadata_source.json --base-path "$SPEC_BASE_PATH" \
    --output /path/to/new/root_discovery
This makes a separate source-only request for each physical table. Other tables
remain available for context. The dispatcher does not supply the right operator,
join, condition or role. Empty/held proposals remain in the resulting library.
Each request proposes up to four candidates, so this is a bounded discovery pass,
not a guarantee to enumerate every possible rule. More than eight tables require
explicit --root table_name batches or a larger --maximum-roots (up to32); tables
are never silently skipped. All original source meanings and records are preserved.
The original requested analysis scope takes precedence; a root outside it should
produce no candidate. Use --root to restrict a deliberately scoped analysis.

7. Compose compatible frozen packs and apply a library (CPU)
  "$PYTHON" -B cli.py library-build --packs /path/to/pack_a.json \
    /path/to/pack_b.json --output /path/to/new/library
  "$PYTHON" -B cli.py library-serve --library /path/to/new/library/library.json \
    --input /path/to/same_schema_new_batch.json --bulk \
    --output /path/to/new/library_results
Warning wins OR composition. A no-rule/unknown pack preserves incomplete coverage;
it is not silently discarded to turn an uncertain result into a nonwarning.

Outputs and boundaries
The model proposes source columns, scope, typed conditions, exceptions and roles.
Code calculates and validates exact values and source/version pins. Administrators
still review recommendations before adoption. New Boolean features must not be
silently inserted into an old ML feature vector. This bundle does not provide
backend API/DB/alert delivery, actual ingestion-latency measurement or certified
company incident precision/recall.
The current code also rejects bounded proofs of impossible plans, for example
using the qualifying context itself as an explanation for every reviewed event.
This checks operator logic; it does not prove arbitrary natural-language meaning.
This bundle executes source-bound-reusable-discovery1.1-conditional-row packs.
It rejects legacy1.0 packs rather than silently interpreting them under new
operator semantics. Use the separately preserved old runtime for those old packs.
Regenerate and review a new pack/library before changing execution policies.
Selected generation prompt policy: source-only-scope-review1.5-source-evidence.
The generated AST/pack policy remains source-bound-reusable-discovery1.1-conditional-row.
Selected execution policies are separately pinned in runtime_overlay_manifest.json:
  typed operators: typed-relational-discovery1.3-evidence-dominance
  serving: logical-rule-serving1.5-evidence-dominance
  feature definitions: logical-feature-definition1.3-runtime-binding
An unknown occurrence key in a dependency check is unavailable. An explicitly
null predecessor with known occurrence identity still means no dependency.
With requires_complete_capture=false, conclusive known relation matches or
known multiple matches are not erased by additional unknown alternatives.
Uncertain absence still remains unknown. An explicit requires_complete_capture=true
is preserved as an additional whole-snapshot admission precondition.
Feature definition SHA binds the runtime version, so old measured feature IDs
must not be silently reused under these execution semantics.

Detailed input/output and training notes are in docs/. Use supplied business
definitions honestly; unknown meanings, units or requirements must remain unknown.

Project-specific full CSV replay (CPU; no LLM)
For the reviewed finite-export metadata profile and its frozen generated pack:
  "$PYTHON" -B cli.py replay-esb-export \
    --data-root /path/to/approved/TPM_CSV_directory \
    --source /path/to/reviewed/export_source.json \
    --rules /path/to/frozen/export_candidate_pack.json \
    --output /path/to/new/full_export_replay
For the separate reviewed S/F reported-outcome profile use replay-esb-status with
its matching source and pack. These adapters read the exact declared metadata
columns, preserve unknowns, require finite-export closure, reject cross-family
plans and preserve original source-row evidence. No company CSV is bundled.
They are specific adapters for the reviewed project export profiles, not automatic
CSV schema/meaning inference. They do not authorize new business HR or treat the
finite export as proof that operational logging was complete.

Declared source-reference admission
Policy: declared-self-reference-direction1.0
During strict generation validation, dependency_order links reversed against
an explicit machine-readable self-reference are rejected and returned as code
feedback. The code does not rewrite the AI plan or infer a dependency from
unverified prose. Equality scopes, explicitly bidirectional declarations and
other relation operators are not rejected by this narrow direction check.

Integration request status
run_request.py wraps the same selected CLI and saves request_status.json. Failed,
invalid or context-limited requests remain unavailable with all partial attempts;
there is no automatic truncation, retry, activation, fitting or model fallback.
An exit of completed_for_review does not approve operational adoption.

Bounded multi-condition example
examples/coverage_source.json is a second fictional source with three separately
required fields. Actual model discovery proposed one check in round1 and two more
in round2. After explicit source review, these unchanged executable packs are
composed in examples/coverage_curated_library.json. Use library-serve with that
library and coverage_source.json. This is reviewed research curation, not a claim
that every single call finds every possible rule. The original discovery also
kept a terminal zero-candidate pack; that unmodified full library conservatively
remains unavailable on some otherwise nonwarning inputs. It is not silently
discarded by the runtime. No operating approval is given by this example.

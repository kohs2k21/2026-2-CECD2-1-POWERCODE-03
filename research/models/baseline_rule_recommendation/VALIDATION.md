# Baseline SR research-only delivery validation

Validated in the project Docker container and existing project Python environment.
No packages were installed and no new SFT or ML fit ran. This publication includes
only `research/`. Backend integration, deployment and merge are not performed.

| Check | Result |
|---|---|
| CPU tests contained in this research package | 14/14, including schema and saved-response checks |
| Prior full local prototype CPU tests | 33/33; includes server-side tests not published in this delivery |
| Saved actual outputs revalidated using research API alone | 3/3; no new model calls |
| Actual first-response gate | 1/3; two identity-copy errors caught and corrected within one repair per case |
| Actual pinned Qwen Rule generation with compact JSON grammar | 3/3 controlled cases |
| Sufficient reference/current data | proposed; one F.total_count > 4.0 service draft |
| Fewer than 20 valid reference values | request_evidence; no executable draft |
| Known reference, zero valid current observations | deferred; condition retained, no executable draft |
| Preserved original feature_rule_spec files | 313/313 SHA256 unchanged; package verifier separately checks 312 manifested assets |
| DB end-to-end | Not run: psycopg and isolated PostgreSQL unavailable |
| Backend/frontend build and tests | Not run: Node/pnpm and node_modules unavailable |

The historical actual generation inputs, raw responses, tokens, model SHA, validation and drafts
are in [the controlled generation evidence](examples/actual_service_generation.json).
These are three controlled development cases, not an independent business-rule
benchmark or anomaly-detection accuracy score. They do not evaluate customer data.
Administrator meaning review and operational integration remain required.

The unchanged service Feature adapter is v5 (weight SHA begins 1b3d7456), not the
silver51 default of the separate final research package. Rule uses the fixed
existing LoRA d816cf6a1f264149fb3d4db9c7cc929a76b12ce6f04f0407a4c4b459dbd573c9.

The raw shortage response still uses punctuation-only unavailable_reason text and the deferred warning_meaning is only SR; its main reason and needed key identify the missing sample evidence. The backend should explain no_rule using the fixed contract label and NEEDED_LABELS. Full natural-language quality is not approved.

Earlier failed checks are preserved in the local audit: an embedding lookup after
PEFT wrapping, an incorrect current-absence explanation, and repeated trailing
JSON tabs reaching the output budget. The loader now resolves embeddings before
wrapping; the explanation is checked for conditional wording; compact grammar
rejects the repeated-tab construction. An obsolete whitespace-permitting smoke
run was interrupted only after ownership/purpose verification; its completed
cases and stop reason were retained. Earlier results were not overwritten.

The research core and prompt retain the bytes used by the local model evaluation.
Handoff documentation, a materialized effective schema and standalone contract
tests were added for this publication. The tested local server exporter and worker
changes are not included. The backend must recreate and verify its inference and
integration path using [BACKEND_HANDOFF.md](BACKEND_HANDOFF.md); publication alone
does not switch an existing deployment. Previous operational scores and training
history are not current test results.

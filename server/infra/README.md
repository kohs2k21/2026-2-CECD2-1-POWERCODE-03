# Server infrastructure

Infrastructure entry points and deployment configuration belong here. `scripts/bootstrap.sh` is a local frontend dependency helper; it does not provision services or contact production systems.

`migrations/001_postgres_queues.sql` creates two independent, isolated PostgreSQL queue tables. Apply only to a designated development or benchmark database after review. It does not create the draft ERD tables. The worker transport benchmark is documented in `server/worker/README.md`.

`migrations/002_core_erd.sql` defines the supplied 24-table PostgreSQL ERD in one transaction. It creates enums, tables, indexes and selected checks/FKs; nullable circular references are added after their target tables. Apply it only to a fresh development database after inspecting the SQL and taking a backup. It has not been executed against the server database. The migration deliberately does not move the existing JSON/JWT account store.

The ERD's operational invariants still require application code: source/layer checks for typed rows, same-source/as-of parent validation, sensitive-field projection before any JSON/hash/file write, snapshot/config immutability, feature DSL validation, artifact compatibility, idempotent job claim/fencing, and active-model swap reconciliation. A schema alone does not make Collector, Trainer, Detector, or API paths functional.

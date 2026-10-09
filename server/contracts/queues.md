# Worker queue contract (draft v1)

Detection and training use separate logical queues and separate consumers/workers. PostgreSQL tables are the first implementation; Redis Streams and Kafka remain candidates for a later comparison under equivalent load. Names below are logical identifiers.

| Queue | Producer | Consumer | Payload | Priority |
|---|---|---|---|---|
| `detection.logs.v1` (`powercode_queue.detection_logs`) | Collector | long-running Detector | projected log reference | latency-sensitive |
| `training.jobs.v1` (`powercode_queue.training_jobs`) | Express job API | Trainer | persisted training job ID | throughput-oriented |

Neither worker consumes from the other queue. Deploy and scale them independently; training must not occupy detection consumers or their GPU capacity reservation. A shared broker is allowed, but each queue needs its own consumer group, lag metric, retry policy, and dead-letter destination.

## Detection message

```json
{"schemaVersion":1,"eventId":"uuid","observationId":"uuid","enqueuedAt":"ISO-8601 UTC"}
```

The Collector projects sensitive fields out **before** persisting or publishing. The message points to a persisted, sanitized observation; it contains no BODY, messageBody, credentials, or raw source payload. The final identity and revision rules for `observationId` remain pending the ERD decision. Detector writes a result idempotently for the observation and model version, then acknowledges the message. On failure it retries with bounded attempts and moves exhausted messages to a detection-only dead-letter destination.

## Training message

```json
{"schemaVersion":1,"jobId":"uuid","enqueuedAt":"ISO-8601 UTC"}
```

Express first persists a training job and returns its ID; it publishes only the ID after the job is durable. Trainer atomically claims the job before starting work. Duplicate delivery must not start a second training run. Job state, progress, failure reason, artifact reference, and selected input snapshot live in the database, not in queue messages. The job's model cannot become the active Detector model without an explicit approval and replacement step. Failed jobs have a separate retry/dead-letter policy from detection.

With the core ERD, `jobId` refers to `run_jobs.id`. The physical queue table intentionally has no FK so an isolated transport benchmark can use synthetic IDs; production publishers must validate the job and publish in the same transaction or through a durable outbox.

## Delivery and recovery

- Assume at-least-once delivery. Acknowledge only after the corresponding database state is committed.
- Assign independent concurrency limits and retry/dead-letter destinations. Detection backlog must not be blocked by long training runs.
- Track queue depth/oldest age, attempts, processing time, dead-letter count, and worker health separately for each queue.
- Define durable publish/outbox behavior with the PostgreSQL job/observation migrations; a successful DB commit must not silently lose its queue message.
- The initial PostgreSQL queue schema is isolated from the draft domain ERD. Do not publish production messages until domain tables and identity rules are finalized.

# Trainer worker

Reserved for a separately deployed Python worker consuming only `training.jobs.v1`. It loads durable job definitions from PostgreSQL, claims each job atomically, trains from an approved snapshot, and records progress and model artifacts. It does not serve Express requests or consume detection logs.

The queue message and failure rules are defined in `server/contracts/queues.md`. No Trainer process or training-job API is implemented yet.

`lease.py` provides a heartbeat for long training runs. It renews both the queue lease and the current `run_job_attempts` lease in one database transaction. The caller must stop training if `heartbeat.lost` becomes true. Before recording a final model/result, the caller must call `lock_training_ownership` in the same transaction, write the result, acknowledge the queue message, and roll back everything if acknowledgement fails. The Trainer runtime that invokes these helpers is still pending.

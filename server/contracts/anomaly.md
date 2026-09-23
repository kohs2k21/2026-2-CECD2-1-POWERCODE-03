# Anomaly contract v1

Every endpoint below requires `Authorization: Bearer <JWT>`. A user token may evaluate logs and subscribe to the stream; only an admin token may ingest a log into the stream.

## Raw log input

The required fields are finite `anomalyScore` in `[0, 1]`, non-negative finite `processTimeMs`, and a non-empty `responseCode`. Optional string fields are `logId`, `detectedAt`, `processName`, `channelName`, `transactionId`, and `status`.

## Risk evaluation

`POST /api/anomaly/evaluate-risk` accepts `{ "logs": [<raw-log>] }` and returns:

```json
{
  "schemaVersion": 1,
  "message": "Successfully evaluated anomaly risk levels.",
  "results": [
    {
      "logId": "optional-source-id",
      "riskScore": 42,
      "riskLevel": 2,
      "severity": "Warning"
    }
  ]
}
```

Invalid payloads return `400` with `code: "invalid_log_payload"`.

## Authenticated SSE

`GET /api/anomaly/realtime-stream` opens an SSE stream. The server sends `event: anomaly` with an `id` and this event data shape:

```json
{
  "schemaVersion": 1,
  "eventId": "source-id-or-generated-id",
  "provenance": "backend-ingest",
  "detectedAt": "2026-01-01T00:00:00.000Z",
  "processName": "optional",
  "channelName": "optional",
  "transactionId": "optional",
  "status": "optional",
  "responseCode": "E123",
  "anomalyScore": 0.5,
  "processTimeMs": 1000,
  "riskScore": 45,
  "riskLevel": 2,
  "severity": "Warning"
}
```

Optional fields are omitted when the ingest payload does not provide them. The service does not fabricate transaction snapshots, LLM reports, or explanations. Clients should deduplicate by `eventId` and close the stream on logout; the API does not promise replay or URL-token authentication.

## Admin ingest

`POST /api/anomaly/logs` accepts one raw log and returns `201` with `{ "schemaVersion": 1, "message": "…", "log": <event> }`. It requires an admin token and broadcasts the same event to authenticated subscribers. A missing/invalid token is rejected before a stream is opened or a log is processed.

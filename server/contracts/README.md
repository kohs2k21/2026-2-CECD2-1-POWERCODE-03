# Runtime contracts

Reserved for versioned request, response, event, and worker contracts shared by deployable server components. Keep definitions dependency-light and independent of `research/` experiments.

- `auth.md` defines login, Bearer `/me`, and disabled registration responses.
- `anomaly.md` defines authenticated risk evaluation, admin ingest, and SSE events.

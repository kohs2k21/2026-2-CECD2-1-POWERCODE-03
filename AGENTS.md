# Repository guidance

- Do not read or print `.env`, `.env.*`, PEM files, private keys, or other secret material.
- Keep `server/` deployable without importing from `research/` or depending on root-level code.
- Keep real and mock operation modes explicit; never silently fall back between them.
- Tests and fixtures must not send external email, call production services, or perform other external side effects.
- Keep research artifacts under `research/`; shared runtime contracts belong under `server/contracts/`.

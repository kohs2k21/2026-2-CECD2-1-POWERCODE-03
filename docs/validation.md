# Validation report

Date: 2026-09-18
Scope: C1 structure-only KSJ relocation into `server/`. The C2 handoff note below records the later staged HUJ relocation.

- `server/frontend` matches the original KSJ `frontend` blobs for 121/121 files. The moved README is the only intentionally edited frontend file.
- `git diff --check`, unmerged-path checks, conflict-marker checks, changed/tracked secret-filename checks, and `server/frontend` root/research import checks pass.
- An isolated copy containing only `server/` installed with `pnpm install --frozen-lockfile`, passed `pnpm exec tsc -b`, and passed `pnpm build` using pnpm 11.1.2 and Node 22.17.0.
- Vite reports its existing large-chunk warning (1.07 MB JavaScript output); the build succeeds.
- `server/infra/scripts/bootstrap.sh` is LF-clean, the indexed blob passes `bash -n`, and the narrow `*.sh text eol=lf` rule is present in `.gitattributes`. `bash -n` and `--help` both pass from an arbitrary-name copy containing only `server/`.

The C1 server backend was a placeholder README, so no backend build or auth behavior test applied at that point. In the staged C2 handoff, the HUJ backend is relocated under `server/backend`; its auth and API behavior remains unchanged for the separate C3 task, and QA owns the backend build check. The mock sender and CSV remain under `server/backend` pending audit. No external services, email, production credentials, or customer/raw log data were used.

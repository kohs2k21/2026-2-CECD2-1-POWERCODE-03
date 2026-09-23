# Validation report

Date: 2026-09-18
Scope: structure-only KSJ relocation into `server/`; no HUJ or runtime behavior changes.

- `server/frontend` matches the original KSJ `frontend` blobs for 121/121 files. The moved README is the only intentionally edited frontend file.
- `git diff --check`, unmerged-path checks, conflict-marker checks, changed/tracked secret-filename checks, and `server/frontend` root/research import checks pass.
- An isolated copy containing only `server/` installed with `pnpm install --frozen-lockfile`, passed `pnpm exec tsc -b`, and passed `pnpm build` using pnpm 11.1.2 and Node 22.17.0.
- Vite reports its existing large-chunk warning (1.07 MB JavaScript output); the build succeeds.
- `server/infra/scripts/bootstrap.sh` is LF-clean, the indexed blob passes `bash -n`, and the narrow `*.sh text eol=lf` rule is present in `.gitattributes`. `bash -n` and `--help` both pass from an arbitrary-name copy containing only `server/`.

The server backend is a placeholder README in this structure-only change, so no backend build or auth behavior test applies. No external services, email, production credentials, or customer/raw log data were used.

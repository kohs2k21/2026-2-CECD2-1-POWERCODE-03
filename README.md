# 2026-1-CECD1-03-POWERCODE-13
동국대학교 종합 설계 3분반 팀 파워코드 입니다.

## Layout

- `server/frontend/` contains the Vite dashboard.
- `server/backend/` is reserved for the backend integration; the HUJ backend is not part of this structure-only commit.
- `server/worker/`, `server/contracts/`, and `server/infra/` reserve runtime boundaries and infrastructure entry points.
- `research/` contains experiments and evaluation work and is not a server dependency.
- `automation/`, `docs/`, and `.github/workflows/` contain repository-level scaffolds.

From the repository root, run `bash server/infra/scripts/bootstrap.sh` to install frontend dependencies. Add `--with-build` to build the dashboard.

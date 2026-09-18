#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$SERVER_DIR/frontend"
PNPM_VERSION="11.1.2"

run_build=false

for arg in "$@"; do
  case "$arg" in
    --with-build)
      run_build=true
      ;;
    -h|--help)
      cat <<'USAGE'
Usage: server/infra/scripts/bootstrap.sh [--with-build]

Install project dependencies for a fresh local checkout.
The frontend dependency set is resolved from frontend/package.json and pnpm-lock.yaml within the server directory.

Options:
  --with-build  Install dependencies and run the frontend build.
USAGE
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Run server/infra/scripts/bootstrap.sh --help for usage." >&2
      exit 1
      ;;
  esac
done

echo "==> Server directory: $SERVER_DIR"

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required but was not found." >&2
  echo "Install Node.js first, then run this script again." >&2
  exit 1
fi

echo "==> Node: $(node --version)"

if command -v corepack >/dev/null 2>&1; then
  echo "==> Enabling pnpm $PNPM_VERSION with corepack"
  corepack enable
  corepack prepare "pnpm@$PNPM_VERSION" --activate
elif ! command -v pnpm >/dev/null 2>&1; then
  echo "pnpm is required but neither corepack nor pnpm was found." >&2
  echo "Install pnpm $PNPM_VERSION, then run this script again." >&2
  exit 1
fi

echo "==> pnpm: $(pnpm --version)"

if [[ ! -d "$FRONTEND_DIR" ]]; then
  echo "Frontend directory not found: $FRONTEND_DIR" >&2
  exit 1
fi

echo "==> Installing frontend dependencies from $FRONTEND_DIR/package.json and pnpm-lock.yaml"
pnpm --dir "$FRONTEND_DIR" install --frozen-lockfile

if [[ "$run_build" == true ]]; then
  echo "==> Building frontend"
  pnpm --dir "$FRONTEND_DIR" build
fi

echo "==> Bootstrap complete"

#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MODEL_DIR="$SCRIPT_DIR/../../models/hybrid_sr_ml/1.1.0"
export PYTHONDONTWRITEBYTECODE=1
exec "${HYBRID_PYTHON:-python3}" -B "$MODEL_DIR/cli.py" "$@"

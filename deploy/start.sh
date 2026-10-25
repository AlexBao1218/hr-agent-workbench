#!/bin/sh
set -eu
# One process owns SQLite and in-process background analysis tasks.
exec python -m uvicorn hr_backend.production:app --host 0.0.0.0 --port "${PORT:-10000}" --workers 1

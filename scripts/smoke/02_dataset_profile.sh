#!/usr/bin/env sh
set -eu

uv run --project services/dataset_spike python -m dataset_spike.spike "$@"

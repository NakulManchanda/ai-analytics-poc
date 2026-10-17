# tooling/benchmarks

Offline scenario replay and benchmark tooling. It is **not** part of the production app image.
It targets either path: `app_runs` (app `/api/runs`) or `gateway_chat` (inference gateway directly).

- Scenario fixtures stay in `config/scenarios/` (single source).
- Run via the root Makefile (`make replay-*`, `make trace-request`) or directly:
  `uv run --project tooling/benchmarks python -m benchmarks.run --scenario <name> --target-url <url>`
- Tests: `make bench-test` (uses `--extra app`, which path-depends on `services/app` for the
  catalogue-alignment and canonical-prefix drift checks in `benchmarks/app_checks.py`; core replay
  imports without the app installed).
- `tooling/evals/` is reserved for the future answer-quality (DeepEval) tooling.

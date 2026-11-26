#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _fail(message: str) -> None:
    raise SystemExit(f"preflight failed: {message}")


def _must_exist(relative_path: str) -> Path:
    path = ROOT / relative_path
    if not path.exists():
        _fail(f"missing required file: {relative_path}")
    return path


def _check_tools() -> None:
    required = ["python3", "make", "docker", "node", "npm", "aws", "jq"]
    missing = [tool for tool in required if shutil.which(tool) is None]
    if missing:
        _fail(f"missing required tools: {', '.join(missing)}")


def _check_no_long_lived_aws_env_keys() -> None:
    access_key = (os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
    if access_key.startswith("AKIA"):
        _fail("detected static AWS access key in environment (AKIA*); use SSO or OIDC")
    if secret_key:
        _fail("AWS_SECRET_ACCESS_KEY is set; use temporary credentials, not long-lived keys")


def _check_dataset_contract() -> None:
    _must_exist("tooling/evals/datasets/golden/v1/cases.jsonl")
    holdout = _must_exist("tooling/evals/datasets/holdout/v1/cases.jsonl")
    splits_path = _must_exist("tooling/evals/datasets/golden/v1/splits.json")
    data = json.loads(splits_path.read_text(encoding="utf-8"))

    if data.get("version") != "v2":
        _fail("tooling/evals/datasets/golden/v1/splits.json must have version=v2")

    holdout_dataset = data.get("holdout_dataset")
    if holdout_dataset != "datasets/holdout/v1/cases.jsonl":
        _fail("splits.json holdout_dataset must point to datasets/holdout/v1/cases.jsonl")

    expected_splits = ("dev", "regression", "holdout")
    for split in expected_splits:
        values = data.get(split)
        if not isinstance(values, list) or not values:
            _fail(f"split '{split}' must be a non-empty list")

    all_case_ids: list[str] = []
    for split in expected_splits:
        all_case_ids.extend(data[split])
    if len(all_case_ids) != len(set(all_case_ids)):
        _fail("splits.json contains duplicate case IDs across splits")

    holdout_lines = [line for line in holdout.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(holdout_lines) < len(data["holdout"]):
        _fail("holdout dataset has fewer rows than holdout split case IDs")


def _check_litellm_and_bedrock_contract() -> None:
    config = _must_exist("infra/litellm/config.yaml").read_text(encoding="utf-8")
    for alias in ("taxi-planner", "taxi-answer", "taxi-classifier"):
        if alias not in config:
            _fail(f"LiteLLM config missing model alias: {alias}")

    aws_override = _must_exist("docker-compose.aws.yml").read_text(encoding="utf-8")
    required = (
        "LLM_PROVIDER: bedrock",
        "DYNAMODB_TABLE_NAME:",
        "GLOBAL_BEDROCK_MONTHLY_LIMIT_USD",
        "AWS_PROFILE",
    )
    for marker in required:
        if marker not in aws_override:
            _fail(f"docker-compose.aws.yml missing marker: {marker}")


def _check_budget_and_feature_flags() -> None:
    config_spec = importlib.util.spec_from_file_location(
        "preflight_app_config", ROOT / "services/app/app/config.py"
    )
    budget_spec = importlib.util.spec_from_file_location(
        "preflight_app_budgets", ROOT / "services/app/app/orchestration/budgets.py"
    )
    if not config_spec or not config_spec.loader or not budget_spec or not budget_spec.loader:
        _fail("unable to load app config/budget modules for preflight")

    config_module = importlib.util.module_from_spec(config_spec)
    budget_module = importlib.util.module_from_spec(budget_spec)
    sys.modules[config_spec.name] = config_module
    sys.modules[budget_spec.name] = budget_module
    config_spec.loader.exec_module(config_module)
    budget_spec.loader.exec_module(budget_module)

    settings = config_module.Settings.from_environment()
    try:
        Decimal(settings.global_bedrock_monthly_limit_usd)
    except InvalidOperation:
        _fail("GLOBAL_BEDROCK_MONTHLY_LIMIT_USD must parse as a decimal number")

    _ = budget_module.ExecutionBudgets.from_env()


def check_env() -> None:
    _check_tools()
    _check_no_long_lived_aws_env_keys()
    _check_dataset_contract()
    _check_litellm_and_bedrock_contract()
    _check_budget_and_feature_flags()


def check_services() -> None:
    compose = _must_exist("docker-compose.yml").read_text(encoding="utf-8")
    for marker in ("redis:", "mcp:", "REDIS_URL:", "MCP_URL:"):
        if marker not in compose:
            _fail(f"docker-compose.yml missing service marker: {marker}")

    observability = _must_exist("docker-compose.observability.yml").read_text(encoding="utf-8")
    for marker in ("otel-collector", "prometheus", "grafana"):
        if marker not in observability:
            _fail(f"docker-compose.observability.yml missing marker: {marker}")

    _must_exist("scripts/smoke/00_health.sh")
    _must_exist("scripts/smoke/15_litellm_run.sh")


def check_eval() -> None:
    _must_exist("tooling/evals/fixtures/baseline/manifest.json")
    _must_exist("tooling/evals/fixtures/candidate/manifest.json")
    _must_exist("tooling/evals/tests/test_prompt_regression_gate.py")
    _must_exist("tooling/evals/tests/test_make_eval_targets.py")

    running_evals = _must_exist("docs/evaluation/running-evaluations.md").read_text(
        encoding="utf-8"
    )
    for marker in ("EVAL_CONFIRM=1", "baseline"):
        if marker not in running_evals:
            _fail(f"docs/evaluation/running-evaluations.md missing marker: {marker}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Repository preflight checks")
    parser.add_argument("mode", choices=("env", "services", "eval", "all"))
    args = parser.parse_args()

    if args.mode in ("env", "all"):
        check_env()
        print("preflight env: ok")
    if args.mode in ("services", "all"):
        check_services()
        print("preflight services: ok")
    if args.mode in ("eval", "all"):
        check_eval()
        print("preflight eval: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

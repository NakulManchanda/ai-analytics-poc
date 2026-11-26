#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
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


def validate_aws_credentials_env(env: Mapping[str, str] | None = None) -> str | None:
    """Return a validation error, or None when AWS env credentials are acceptable.

    Accepted:
    - no explicit AWS credential env vars (SDK/provider chain still allowed)
    - temporary session credentials with all three vars

    Rejected:
    - permanent AKIA* credentials
    - incomplete credential sets
    - access/secret without session token
    """

    source = os.environ if env is None else env
    access_key = (source.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret_key = (source.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    session_token = (source.get("AWS_SESSION_TOKEN") or "").strip()

    if not (access_key or secret_key or session_token):
        return None

    if not (access_key and secret_key and session_token):
        return (
            "incomplete AWS credential env vars; require temporary session credentials "
            "(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN)"
        )

    if access_key.startswith("AKIA"):
        return "detected permanent AWS access key in environment (AKIA*); use SSO or OIDC session credentials"

    if not session_token:
        return "AWS_SESSION_TOKEN is required for temporary session credentials"

    return None


def _check_aws_credentials_env() -> None:
    error = validate_aws_credentials_env()
    if error:
        _fail(error)


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

    holdout_lines = [
        line for line in holdout.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
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
    if (
        not config_spec
        or not config_spec.loader
        or not budget_spec
        or not budget_spec.loader
    ):
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


def _run_checked(
    command: list[str],
    *,
    label: str,
    env_overrides: Mapping[str, str] | None = None,
    cwd: Path = ROOT,
) -> None:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip().splitlines()
        tail = stderr[-1] if stderr else f"exit={result.returncode}"
        _fail(f"{label} failed ({tail})")


def check_env() -> None:
    _check_tools()
    _check_aws_credentials_env()
    _check_dataset_contract()
    _check_litellm_and_bedrock_contract()
    _check_budget_and_feature_flags()


def check_services() -> None:
    compose = _must_exist("docker-compose.yml").read_text(encoding="utf-8")
    for marker in ("redis:", "mcp:", "REDIS_URL:", "MCP_URL:"):
        if marker not in compose:
            _fail(f"docker-compose.yml missing service marker: {marker}")

    observability = _must_exist("docker-compose.observability.yml").read_text(
        encoding="utf-8"
    )
    for marker in ("otel-collector", "prometheus", "grafana"):
        if marker not in observability:
            _fail(f"docker-compose.observability.yml missing marker: {marker}")

    _must_exist("scripts/smoke/00_health.sh")
    _must_exist("scripts/smoke/01_mcp_empty.sh")
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


def check_runtime_local() -> None:
    app_url = os.getenv("AI_APP_URL", "http://127.0.0.1:8080")
    mcp_url = os.getenv("MCP_URL", "http://127.0.0.1:8001/mcp")

    _run_checked(
        ["bash", "scripts/smoke/00_health.sh"],
        label="app health smoke",
        env_overrides={"AI_APP_URL": app_url},
    )
    _run_checked(
        ["bash", "scripts/smoke/01_mcp_empty.sh"],
        label="mcp/duckdb smoke",
        env_overrides={"MCP_URL": mcp_url},
    )
    _run_checked(
        ["curl", "--fail", "--silent", "--show-error", f"{app_url}/api/status"],
        label="status endpoint (redis/sse contract)",
    )


def check_runtime_aws() -> None:
    check_runtime_local()

    _run_checked(["make", "--no-print-directory", "litellm-ready"], label="litellm readiness")
    _run_checked(["aws", "sts", "get-caller-identity"], label="host aws identity")

    compose_files = [
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.aws.yml",
        "-f",
        "docker-compose.litellm.yml",
        "-f",
        "docker-compose.phoenix.yml",
        "-f",
        "docker-compose.phoenix-litellm.yml",
    ]

    _run_checked(
        [
            "docker",
            "compose",
            *compose_files,
            "exec",
            "-T",
            "app",
            "python",
            "-c",
            (
                "import os,boto3; "
                "ak=os.getenv('AWS_ACCESS_KEY_ID') or ''; "
                "sk=os.getenv('AWS_SECRET_ACCESS_KEY') or ''; "
                "st=os.getenv('AWS_SESSION_TOKEN') or ''; "
                "assert (not ak and not sk and not st) or (ak and sk and st), 'incomplete aws env credentials'; "
                "assert not ak.startswith('AKIA'), 'permanent key injected into container'; "
                "sess=boto3.session.Session(); "
                "creds=sess.get_credentials(); "
                "assert creds is not None, 'no aws credentials available in app container'; "
                "sess.client('sts').get_caller_identity(); "
                "table=os.getenv('DYNAMODB_TABLE_NAME') or ''; "
                "sess.client('dynamodb').describe_table(TableName=table) if table else None"
            ),
        ],
        label="aws credential propagation + dynamodb permission",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Repository preflight checks")
    parser.add_argument(
        "mode",
        choices=("env", "services", "eval", "runtime-local", "runtime-aws", "all"),
    )
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
    if args.mode in ("runtime-local", "all"):
        check_runtime_local()
        print("preflight runtime-local: ok")
    if args.mode == "runtime-aws":
        check_runtime_aws()
        print("preflight runtime-aws: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The proxy's model mapping, the price table and the IAM allowlist must agree (#207).

infra/litellm/config.yaml is also the deployed proxy's config (Terraform embeds it), so these tests
guard the deployed mapping too. Local untracked overrides are not covered.
"""

from pathlib import Path

import pytest
import yaml
from app.model_policy import RunBudgetExceededError, RunBudgets
from app.model_policy.pricing import (
    ALIAS_MODELS,
    MODEL_RATES,
    default_pricing,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / "infra" / "litellm" / "config.yaml"
VARIABLES = REPO_ROOT / "infra" / "terraform" / "variables.tf"
ARN = "arn:aws:bedrock:us-east-1::foundation-model/{}"


def _proxy_models() -> dict[str, str]:
    entries = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["model_list"]
    return {
        e["model_name"]: e["litellm_params"]["model"].removeprefix("bedrock/")
        for e in entries
    }


def test_planner_targets_nova_pro_and_the_rest_nova_micro() -> None:
    models = _proxy_models()
    assert models["taxi-planner"] == "amazon.nova-pro-v1:0"
    assert models["taxi-answer"] == "amazon.nova-micro-v1:0"
    assert models["taxi-classifier"] == "amazon.nova-micro-v1:0"


def test_every_proxy_alias_is_priced_for_the_model_it_actually_targets() -> None:
    models = _proxy_models()
    assert ALIAS_MODELS == models
    table = default_pricing(tuple(models))
    for alias, model in models.items():
        assert table.rates[alias] == MODEL_RATES[model]


def test_every_proxy_model_is_in_the_terraform_iam_allowlist() -> None:
    variables = VARIABLES.read_text(encoding="utf-8")
    for model in set(_proxy_models().values()):
        assert variables.count(ARN.format(model)) == 2  # default + validation


def test_unmapped_alias_and_unknown_model_are_unpriced_not_guessed() -> None:
    table = default_pricing(
        ("custom-alias", "amazon.nova-lite-v1:0", "amazon.nova-micro-v1:0")
    )
    assert table.estimate("custom-alias", 1_000_000, 0) is None
    assert table.estimate("amazon.nova-lite-v1:0", 1_000_000, 0) is None
    assert "custom-alias" not in table.rates
    # the direct Bedrock path uses a priced model id as its alias
    assert (
        table.rates["amazon.nova-micro-v1:0"] == MODEL_RATES["amazon.nova-micro-v1:0"]
    )


def test_unpriced_call_is_denied_by_a_usd_cap_and_allowed_by_a_token_cap() -> None:
    estimate = default_pricing(("custom-alias",)).estimate("custom-alias", 1000, 100)
    assert estimate is None
    with pytest.raises(RunBudgetExceededError):
        RunBudgets(max_usd=1.0).reserve("run_usd", 1100, estimate)
    # no USD cap configured: the token cap alone applies, as before
    assert RunBudgets(max_tokens=10_000).reserve("run_tok", 1100, estimate)

"""The proxy's model mapping, the price table and the IAM allowlist must agree (#207).

infra/litellm/config.yaml is also the deployed proxy's config (Terraform embeds it), so these tests
guard the deployed mapping too. Local untracked overrides are not covered.
"""

from pathlib import Path

import pytest
import yaml
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


def test_unmapped_alias_is_priced_at_the_highest_known_rate() -> None:
    table = default_pricing(("custom-alias", "amazon.nova-micro-v1:0"))
    assert table.rates["custom-alias"] == max(MODEL_RATES.values())
    # the direct Bedrock path uses the model id as its alias
    assert (
        table.rates["amazon.nova-micro-v1:0"] == MODEL_RATES["amazon.nova-micro-v1:0"]
    )
    assert table.estimate("custom-alias", 1_000_000, 0) == pytest.approx(0.80)

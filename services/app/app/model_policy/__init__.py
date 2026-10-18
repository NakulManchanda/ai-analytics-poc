"""Model-role policy (#170): role->alias mapping, entitlements, per-run budgets, pricing.

Layers of spend control, strongest first:
1. DynamoDB monthly Bedrock guard (`app.bedrock_budget`) -- AUTHORITATIVE hard cap, unchanged.
2. Per-run budgets here -- narrower, in-process, per-run protection (not a monthly cap).
3. Proxy budgets (`infra/litellm/config.yaml`) -- defense in depth, not authoritative.
"""

from app.model_policy.budget import (
    Reservation,
    RunBudgetExceededError,
    RunBudgets,
)
from app.model_policy.context import CallContext, call_context, current_call_context
from app.model_policy.policy import (
    ModelPolicy,
    PolicyConfigError,
    PolicyDeniedError,
    RolePolicy,
)
from app.model_policy.pricing import PricingTable, default_pricing
from app.model_policy.runtime import ModelPolicyRuntime

__all__ = [
    "CallContext",
    "ModelPolicy",
    "ModelPolicyRuntime",
    "PolicyConfigError",
    "PolicyDeniedError",
    "PricingTable",
    "Reservation",
    "RolePolicy",
    "RunBudgetExceededError",
    "RunBudgets",
    "call_context",
    "current_call_context",
    "default_pricing",
]

from dataclasses import dataclass, field

from app.model_policy.budget import RunBudgets
from app.model_policy.policy import ModelPolicy
from app.model_policy.pricing import PricingTable, default_pricing


@dataclass
class ModelPolicyRuntime:
    policy: ModelPolicy
    pricing: PricingTable
    budgets: RunBudgets = field(default_factory=RunBudgets)

    @classmethod
    def default(
        cls,
        planner_alias: str,
        answer_alias: str,
        *,
        policy_file: str | None = None,
        run_max_tokens: int | None = None,
        run_max_usd: float | None = None,
    ) -> "ModelPolicyRuntime":
        policy = (
            ModelPolicy.from_file(policy_file)
            if policy_file
            else ModelPolicy.default(planner_alias, answer_alias)
        )
        return cls(
            policy,
            default_pricing(tuple(policy.providers)),
            RunBudgets(run_max_tokens, run_max_usd),
        )

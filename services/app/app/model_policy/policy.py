"""Role -> alias mapping, tenant entitlements, per-call caps, and allowlisted fallbacks.

Static config only: no LLM-based routing. All roles may point at one alias today."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROLES = ("classifier", "planner", "answer")
ALL_TENANTS = "*"


class PolicyConfigError(ValueError):
    """The policy file is inconsistent (unknown alias, cross-provider fallback, ...)."""


class PolicyDeniedError(Exception):
    """The tenant may not use the model the role maps to."""


@dataclass(frozen=True)
class RolePolicy:
    alias: str
    max_tokens: int | None = None  # per-call output cap
    fallbacks: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelPolicy:
    roles: Mapping[str, RolePolicy]
    providers: Mapping[str, str]  # alias -> provider ("bedrock")
    # tenant -> aliases it may use; "*" is the default. Empty mapping = everyone, all aliases.
    tenant_allowlists: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # Cross-provider fallback means data egress to another vendor: off unless explicit.
    allow_cross_provider_fallback: bool = False

    def __post_init__(self) -> None:
        for role, rp in self.roles.items():
            if role not in ROLES:
                raise PolicyConfigError(f"unknown role {role!r}")
            for alias in (rp.alias, *rp.fallbacks):
                if alias not in self.providers:
                    raise PolicyConfigError(
                        f"role {role!r} uses unknown alias {alias!r}"
                    )
            for fb in rp.fallbacks:
                if (
                    self.providers[fb] != self.providers[rp.alias]
                    and not self.allow_cross_provider_fallback
                ):
                    raise PolicyConfigError(
                        f"fallback {fb!r} for {role!r} crosses providers; not allowed"
                    )

    @classmethod
    def default(
        cls,
        planner_alias: str,
        answer_alias: str,
        classifier_alias: str = "taxi-classifier",
    ) -> "ModelPolicy":
        aliases = {classifier_alias, planner_alias, answer_alias}
        return cls(
            roles={
                "classifier": RolePolicy(classifier_alias),
                "planner": RolePolicy(planner_alias, max_tokens=512),
                "answer": RolePolicy(answer_alias, max_tokens=1024),
            },
            providers={a: "bedrock" for a in aliases},
        )

    @classmethod
    def from_file(cls, path: str) -> "ModelPolicy":
        raw = json.loads(Path(path).read_text())
        return cls(
            roles={
                role: RolePolicy(
                    v["alias"], v.get("max_tokens"), tuple(v.get("fallbacks", ()))
                )
                for role, v in raw["roles"].items()
            },
            providers=dict(raw["providers"]),
            tenant_allowlists={
                t: frozenset(a) for t, a in raw.get("tenant_allowlists", {}).items()
            },
            allow_cross_provider_fallback=bool(
                raw.get("allow_cross_provider_fallback")
            ),
        )

    def entitled(self, tenant_id: str, alias: str) -> bool:
        if not self.tenant_allowlists:
            return True
        allowed = self.tenant_allowlists.get(
            tenant_id, self.tenant_allowlists.get(ALL_TENANTS)
        )
        return allowed is not None and alias in allowed

    def resolve(self, role: str, tenant_id: str) -> RolePolicy:
        rp = self.roles.get(role)
        if rp is None:
            raise PolicyDeniedError(f"no model configured for role {role!r}")
        if not self.entitled(tenant_id, rp.alias):
            raise PolicyDeniedError(f"tenant {tenant_id!r} may not use {rp.alias!r}")
        return rp

    def allowed_fallbacks(
        self, rp: RolePolicy, tenant_id: str
    ) -> tuple[list[str], list[str]]:
        """(permitted, rejected) fallbacks for this tenant."""
        ok = [a for a in rp.fallbacks if self.entitled(tenant_id, a)]
        return ok, [a for a in rp.fallbacks if a not in ok]

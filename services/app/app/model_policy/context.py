"""Per-call identity for policy decisions.

The orchestration loop (not owned by #170) binds tenant and run with `call_context`;
without a binding the tenant is "default" and no per-run budget is applied.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

DEFAULT_TENANT = "default"


@dataclass(frozen=True)
class CallContext:
    tenant_id: str = DEFAULT_TENANT
    run_id: str | None = None
    eval_run_id: str | None = None


_CONTEXT: ContextVar[CallContext | None] = ContextVar("model_policy_call", default=None)


def current_call_context() -> CallContext:
    return _CONTEXT.get() or CallContext()


@contextmanager
def call_context(
    tenant_id: str = DEFAULT_TENANT,
    run_id: str | None = None,
    eval_run_id: str | None = None,
) -> Iterator[None]:
    token = _CONTEXT.set(CallContext(tenant_id, run_id, eval_run_id))
    try:
        yield
    finally:
        _CONTEXT.reset(token)

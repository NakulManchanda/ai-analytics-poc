"""Thread-safe per-run token/USD budgets with reserve -> commit/release and an honest ledger."""

import threading
from dataclasses import dataclass, field


class RunBudgetExceededError(Exception):
    """The run has no remaining budget for the requested reservation."""


@dataclass(frozen=True)
class Reservation:
    run_id: str
    tokens: int
    usd: float | None


@dataclass
class _RunState:
    used_tokens: int = 0
    used_usd: float = 0.0
    held_tokens: int = 0
    held_usd: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    incomplete: bool = False


@dataclass
class RunBudgets:
    max_tokens: int | None = None
    max_usd: float | None = None
    _runs: dict[str, _RunState] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve(self, run_id: str, tokens: int, usd: float | None) -> Reservation:
        """Atomically hold worst-case spend, or refuse. Concurrent callers cannot overshoot."""
        with self._lock:
            state = self._runs.setdefault(run_id, _RunState())
            if (
                self.max_tokens is not None
                and state.used_tokens + state.held_tokens + tokens > self.max_tokens
            ):
                raise RunBudgetExceededError("run token budget exhausted")
            if (
                self.max_usd is not None
                and usd is not None
                and state.used_usd + state.held_usd + usd > self.max_usd
            ):
                raise RunBudgetExceededError("run USD budget exhausted")
            state.held_tokens += tokens
            state.held_usd += usd or 0.0
            return Reservation(run_id, tokens, usd)

    def release(self, reservation: Reservation) -> None:
        """Return a hold for a call that never produced billable usage (failure, retry)."""
        with self._lock:
            state = self._runs[reservation.run_id]
            state.held_tokens -= reservation.tokens
            state.held_usd -= reservation.usd or 0.0

    def commit(
        self,
        reservation: Reservation,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        usd: float | None,
    ) -> None:
        """Replace the hold with actual spend. Unknown usage keeps the worst-case hold as
        spend (conservative) and flags the run's totals incomplete."""
        with self._lock:
            state = self._runs[reservation.run_id]
            state.held_tokens -= reservation.tokens
            state.held_usd -= reservation.usd or 0.0
            state.calls += 1
            if input_tokens is None or output_tokens is None:
                state.used_tokens += reservation.tokens
                state.used_usd += reservation.usd or 0.0
                state.incomplete = True
                return
            state.input_tokens += input_tokens
            state.output_tokens += output_tokens
            state.used_tokens += input_tokens + output_tokens
            if usd is None:
                state.incomplete = True
            else:
                state.used_usd += usd

    def summary(self, run_id: str) -> dict[str, object]:
        """Totals count only known usage; `incomplete` marks a lower bound."""
        with self._lock:
            s = self._runs.get(run_id, _RunState())
            return {
                "calls": s.calls,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "budget_tokens_used": s.used_tokens,
                "budget_usd_used": round(s.used_usd, 9),
                "incomplete": s.incomplete,
            }

    def forget(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

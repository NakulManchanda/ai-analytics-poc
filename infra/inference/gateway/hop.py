"""Gateway-owned hop policy; actual token identity and consumption belong to the worker.

Placement cache beliefs are hints only. Request-supplied connector parameters never
cross this boundary. Enabling the lab connector is an explicit operator choice.
"""

from __future__ import annotations

import os
import time

EXPERIMENT_CASES = {
    "same_worker_local_reuse",
    "cross_worker_recompute_transfer_disabled",
    "independently_warmed_destination_local_hit",
    "real_mooncake_transfer_consumed",
}


def prepare(
    body: dict,
    decision,
    correlation: dict,
    remaining_s: float | None,
    override: str | None = None,
    experiment_case: str | None = None,
) -> tuple[dict, dict]:
    payload = dict(body)
    payload.pop("kv_transfer_params", None)
    if os.getenv("KV_HOP_ENABLED") != "1":
        return payload, {"hop_decision_reason": "disabled", "hop_result": "not_attempted"}
    allowed = remaining_s is None or remaining_s >= 1.5
    if decision.prior_worker == decision.chosen_worker:
        reason = "local_prefix_present"
    elif decision.prior_worker is not None:
        reason = "remote_prefix_candidate"
    else:
        reason = "transfer_disabled"
    load = (
        allowed
        and decision.prior_worker is not None
        and decision.prior_worker != decision.chosen_worker
    )
    if os.getenv("ALLOW_EXPERIMENT_CONTROLS") == "1" and override in ("on", "off"):
        load = allowed and override == "on"
        reason = "experiment_on" if override == "on" else "experiment_off"
        if experiment_case in EXPERIMENT_CASES:
            reason = {
                "same_worker_local_reuse": "local_prefix_present",
                "cross_worker_recompute_transfer_disabled": "transfer_disabled",
                "independently_warmed_destination_local_hit": ("independently_warmed_destination"),
                "real_mooncake_transfer_consumed": "remote_prefix_candidate",
            }[experiment_case]
    if not allowed:
        reason = "deadline_recompute"
    params = {
        "lmcache.hop.load": load,
        "lmcache.hop.request_id": correlation["x-request-id"],
        "lmcache.hop.conversation_id": correlation["x-conversation-id"],
        "lmcache.hop.agent_step": correlation["x-agent-step"],
        "lmcache.hop.reason": reason,
    }
    if remaining_s is not None:
        params["lmcache.hop.deadline_epoch_s"] = time.time() + max(0, remaining_s)
    payload["kv_transfer_params"] = params
    return payload, {
        "hop_decision_reason": reason,
        "hop_result": "pending" if load else "recompute",
    }

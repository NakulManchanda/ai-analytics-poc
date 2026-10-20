"""Deterministic validation and grounding guardrails (issue #180).

Everything here is plain Python: no model calls, no prompt text. It sits
between the (probabilistic) model and the (authoritative) governed tools.
"""

from app.validation.grounding import GroundingReport, check_answer_grounding
from app.validation.injection import ObservationFinding, scan_observation
from app.validation.proposal import (
    ProposalValidation,
    Violation,
    summarize_arguments,
    validate_proposal,
)
from app.validation.retry import MAX_VALIDATION_RETRIES, retry_allowed

__all__ = [
    "MAX_VALIDATION_RETRIES",
    "GroundingReport",
    "ObservationFinding",
    "ProposalValidation",
    "Violation",
    "check_answer_grounding",
    "retry_allowed",
    "scan_observation",
    "summarize_arguments",
    "validate_proposal",
]

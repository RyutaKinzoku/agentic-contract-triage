"""Triage routing engine.

Takes the list of :class:`~app.validator.Finding` objects from the validator
and maps them to one of three outcomes: ``AUTO_APPROVE``, ``NEEDS_REVIEW``, or
``QUARANTINE``.

This is the single place where triage decisions are made (SRP). It contains no
policy rules — those belong to the validator. It only asks: given these
findings, what should happen next?

Outcome rules
-------------
- Any ``VIOLATION`` or ``UNKNOWN_COUNTERPARTY`` finding → NEEDS_REVIEW.
- Any ``MISSING`` finding on a *critical* field → NEEDS_REVIEW.
- Any ``LOW_CONFIDENCE`` finding on a *critical* field → NEEDS_REVIEW.
- All findings compliant, no critical issues → AUTO_APPROVE.
- Caller passes ``quarantine=True`` (unreadable / not a contract) → QUARANTINE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.validator import Finding, FindingStatus

# Fields whose absence or low confidence is serious enough to block approval.
CRITICAL_FIELDS = frozenset({
    "counterparty.name",
    "effective_date",
    "contract_type",
    "governing_law",
    "liability_cap",
})


class TriageOutcome(str, Enum):
    """The routing decision for a contract."""

    AUTO_APPROVE = "auto_approve"
    NEEDS_REVIEW = "needs_review"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class TriageResult:
    """The full triage decision for a single contract.

    Attributes:
        outcome: The routing decision.
        reasons: Human-readable explanations for the decision.
        findings: The full list of findings the decision was based on.
    """

    outcome: TriageOutcome
    reasons: list[str]
    findings: list[Finding] = field(default_factory=list)


def route(findings: list[Finding], *, quarantine: bool = False) -> TriageResult:
    """Produce a triage decision from a list of validation findings.

    Args:
        findings: The findings returned by :class:`~app.validator.ContractValidator`.
        quarantine: If True, the file was unreadable or not a contract.

    Returns:
        A :class:`TriageResult` with the outcome and structured reasons.
    """
    if quarantine:
        return TriageResult(
            outcome=TriageOutcome.QUARANTINE,
            reasons=["File could not be read or is not a contract."],
            findings=findings,
        )

    review_reasons: list[str] = []

    for finding in findings:
        if finding.status is FindingStatus.VIOLATION:
            review_reasons.append(finding.reason)

        elif finding.status is FindingStatus.UNKNOWN_COUNTERPARTY:
            review_reasons.append(finding.reason)

        elif finding.status in (
            FindingStatus.MISSING,
            FindingStatus.LOW_CONFIDENCE,
        ) and finding.field in CRITICAL_FIELDS:
            review_reasons.append(finding.reason)

    if review_reasons:
        return TriageResult(
            outcome=TriageOutcome.NEEDS_REVIEW,
            reasons=review_reasons,
            findings=findings,
        )

    return TriageResult(
        outcome=TriageOutcome.AUTO_APPROVE,
        reasons=["All checked fields are compliant."],
        findings=findings,
    )
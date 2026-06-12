"""Contract validation against the policy knowledge base.

:class:`ContractValidator` takes a :class:`~app.schemas.ContractExtraction` and
a :class:`~app.knowledge_base.KnowledgeBase` and produces a list of
:class:`Finding` objects — one per checked term.

Each finding is self-contained: it carries the field it covers, whether a
violation was detected, the human-readable reason, and (for policy violations)
the specific policy rule that was triggered. This makes the output auditable —
the routing engine and the reviewer both see *why*, not just *what*.

Validation logic lives here and nowhere else (SRP). The routing engine consumes
findings but does not re-implement any policy rules.

Design note on confidence gating
---------------------------------
Fields with a confidence below ``MIN_CONFIDENCE`` are skipped rather than
validated. Validating a low-confidence value would produce spurious violations
based on a guess. The finding is recorded as ``LOW_CONFIDENCE`` so the routing
engine still sees that something could not be checked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.knowledge_base import KnowledgeBase

from app.schemas import ContractExtraction, RenewalType

logger = logging.getLogger(__name__)

# Fields with confidence below this value are not validated — the value is
# considered too uncertain to check against a policy rule.
MIN_CONFIDENCE = 0.5

# Minimum cosine similarity for a retrieved policy to be considered relevant.
POLICY_RELEVANCE_THRESHOLD = 0.5


class FindingStatus(str, Enum):
    """Outcome of checking a single contract term."""

    COMPLIANT = "compliant"
    VIOLATION = "violation"
    LOW_CONFIDENCE = "low_confidence"
    UNKNOWN_COUNTERPARTY = "unknown_counterparty"
    MISSING = "missing"


@dataclass(frozen=True)
class Finding:
    """The result of validating a single contract term.

    Attributes:
        field: The contract field that was checked (e.g. 'liability_cap').
        status: The outcome of the check.
        reason: A human-readable explanation of the outcome.
        policy_id: The policy rule that triggered the finding, if any.
        extracted_value: The value that was validated, as a string.
    """

    field: str
    status: FindingStatus
    reason: str
    policy_id: str | None = field(default=None)
    extracted_value: str | None = field(default=None)


class ContractValidator:
    """Validates extracted contract terms against the knowledge base.

    Args:
        kb: The knowledge base to query for policies and clients.
    """

    def __init__(self, kb: "KnowledgeBase") -> None:
        self._kb = kb

    def validate(self, extraction: ContractExtraction) -> list[Finding]:
        """Run all checks and return a finding per validated term.

        Args:
            extraction: The structured contract to validate.

        Returns:
            A list of :class:`Finding` objects, one per checked term.
        """
        findings: list[Finding] = []
        findings.extend(self._check_counterparty(extraction))
        findings.extend(self._check_required_fields(extraction))
        findings.extend(self._check_liability_cap(extraction))
        findings.extend(self._check_renewal(extraction))
        findings.extend(self._check_governing_law(extraction))
        findings.extend(self._check_payment_terms(extraction))
        findings.extend(self._check_termination_notice(extraction))
        return findings

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_counterparty(self, extraction: ContractExtraction) -> list[Finding]:
        """Check whether the counterparty is in the known-clients register.

        Args:
            extraction: The contract to check.

        Returns:
            A single finding for the counterparty field.
        """
        name_field = extraction.counterparty.name
        if not name_field.value:
            return [Finding(
                field="counterparty.name",
                status=FindingStatus.MISSING,
                reason="Counterparty name could not be extracted.",
            )]

        if name_field.confidence < MIN_CONFIDENCE:
            return [Finding(
                field="counterparty.name",
                status=FindingStatus.LOW_CONFIDENCE,
                reason=(
                    f"Counterparty name extracted with low confidence "
                    f"({name_field.confidence:.0%})."
                ),
                extracted_value=name_field.value,
            )]

        match = self._kb.match_counterparty(name_field.value)
        if match is None or not match.is_known:
            matched_info = (
                f" Closest match: '{match.name}' ({match.score:.0%} similarity)."
                if match
                else ""
            )
            return [Finding(
                field="counterparty.name",
                status=FindingStatus.UNKNOWN_COUNTERPARTY,
                reason=(
                    f"'{name_field.value}' was not found in the known-clients "
                    f"register.{matched_info}"
                ),
                extracted_value=name_field.value,
            )]

        return [Finding(
            field="counterparty.name",
            status=FindingStatus.COMPLIANT,
            reason=f"Matched known client '{match.name}' ({match.score:.0%}).",
            extracted_value=name_field.value,
        )]

    def _check_required_fields(
        self, extraction: ContractExtraction
    ) -> list[Finding]:
        """Check that all required fields were extracted.

        Args:
            extraction: The contract to check.

        Returns:
            A finding for each required field that is missing or low-confidence.
        """
        required = {
            "effective_date": extraction.effective_date,
            "contract_type": extraction.contract_type,
            "governing_law": extraction.governing_law,
        }
        findings: list[Finding] = []
        for field_name, extracted in required.items():
            if extracted.value is None:
                findings.append(Finding(
                    field=field_name,
                    status=FindingStatus.MISSING,
                    reason=f"Required field '{field_name}' was not found.",
                ))
            elif extracted.confidence < MIN_CONFIDENCE:
                findings.append(Finding(
                    field=field_name,
                    status=FindingStatus.LOW_CONFIDENCE,
                    reason=(
                        f"'{field_name}' extracted with low confidence "
                        f"({extracted.confidence:.0%})."
                    ),
                    extracted_value=str(extracted.value),
                ))
        return findings

    def _check_liability_cap(
        self, extraction: ContractExtraction
    ) -> list[Finding]:
        """Check whether the liability cap is acceptable per policy.

        Args:
            extraction: The contract to check.

        Returns:
            A finding for the liability_cap field.
        """
        cap = extraction.liability_cap
        if cap.value is None:
            return [Finding(
                field="liability_cap",
                status=FindingStatus.MISSING,
                reason="Liability cap was not found.",
            )]
        if cap.confidence < MIN_CONFIDENCE:
            return [Finding(
                field="liability_cap",
                status=FindingStatus.LOW_CONFIDENCE,
                reason=f"Liability cap extracted with low confidence ({cap.confidence:.0%}).",
                extracted_value=str(cap.value),
            )]

        query = f"liability cap is {cap.value}"
        policies = self._kb.search_policies(query, limit=1)

        unlimited_terms = {"unlimited", "uncapped", "no cap", "no limit"}
        value_lower = cap.value.lower()
        is_unlimited = any(term in value_lower for term in unlimited_terms)

        policy_id = policies[0].policy_id if policies else None

        if is_unlimited:
            return [Finding(
                field="liability_cap",
                status=FindingStatus.VIOLATION,
                reason=(
                    f"Unlimited liability is not acceptable per policy. "
                    f"Extracted: '{cap.value}'."
                ),
                policy_id=policy_id,
                extracted_value=cap.value,
            )]

        return [Finding(
            field="liability_cap",
            status=FindingStatus.COMPLIANT,
            reason=f"Liability cap present: '{cap.value}'.",
            policy_id=policy_id,
            extracted_value=cap.value,
        )]

    def _check_renewal(self, extraction: ContractExtraction) -> list[Finding]:
        """Check auto-renewal notice period against policy.

        Args:
            extraction: The contract to check.

        Returns:
            A finding for renewal terms.
        """
        renewal_type = extraction.renewal.type
        notice = extraction.renewal.notice_period_days

        if renewal_type.value is not RenewalType.AUTO:
            return []

        if notice.value is None:
            return [Finding(
                field="renewal.notice_period_days",
                status=FindingStatus.MISSING,
                reason="Auto-renewal is set but the notice period could not be extracted.",
            )]

        if notice.confidence < MIN_CONFIDENCE:
            return [Finding(
                field="renewal.notice_period_days",
                status=FindingStatus.LOW_CONFIDENCE,
                reason=(
                    f"Renewal notice period extracted with low confidence "
                    f"({notice.confidence:.0%})."
                ),
                extracted_value=str(notice.value),
            )]

        policies = self._kb.search_policies(
            f"auto-renewal notice period is {notice.value} days", limit=1
        )
        policy_id = policies[0].policy_id if policies else None

        if notice.value > 60:
            return [Finding(
                field="renewal.notice_period_days",
                status=FindingStatus.VIOLATION,
                reason=(
                    f"Auto-renewal notice period of {notice.value} days exceeds "
                    f"the 60-day policy maximum."
                ),
                policy_id=policy_id,
                extracted_value=str(notice.value),
            )]

        return [Finding(
            field="renewal.notice_period_days",
            status=FindingStatus.COMPLIANT,
            reason=f"Auto-renewal notice period of {notice.value} days is within policy.",
            policy_id=policy_id,
            extracted_value=str(notice.value),
        )]

    def _check_governing_law(
        self, extraction: ContractExtraction
    ) -> list[Finding]:
        """Check that governing law is an approved jurisdiction.

        Args:
            extraction: The contract to check.

        Returns:
            A finding for governing_law.
        """
        gov_law = extraction.governing_law
        if gov_law.value is None or gov_law.confidence < MIN_CONFIDENCE:
            return []  # already covered by _check_required_fields

        approved = {
            "england and wales",
            "republic of ireland",
            "ireland",
            "delaware",
            "state of delaware",
        }
        value_lower = gov_law.value.lower()
        is_approved = any(j in value_lower for j in approved)

        policies = self._kb.search_policies(
            f"governing law jurisdiction {gov_law.value}", limit=1
        )
        policy_id = policies[0].policy_id if policies else None

        if not is_approved:
            return [Finding(
                field="governing_law",
                status=FindingStatus.VIOLATION,
                reason=(
                    f"Jurisdiction '{gov_law.value}' is not in the approved list "
                    f"(England and Wales, Republic of Ireland, Delaware)."
                ),
                policy_id=policy_id,
                extracted_value=gov_law.value,
            )]

        return [Finding(
            field="governing_law",
            status=FindingStatus.COMPLIANT,
            reason=f"Governing law '{gov_law.value}' is an approved jurisdiction.",
            policy_id=policy_id,
            extracted_value=gov_law.value,
        )]

    def _check_payment_terms(
        self, extraction: ContractExtraction
    ) -> list[Finding]:
        """Check net payment days are within the allowed window.

        Args:
            extraction: The contract to check.

        Returns:
            A finding for payment_terms.net_days, or empty if not present.
        """
        net_days = extraction.payment_terms.net_days
        if net_days.value is None or net_days.confidence < MIN_CONFIDENCE:
            return []

        policies = self._kb.search_policies(
            f"payment terms net {net_days.value} days", limit=1
        )
        policy_id = policies[0].policy_id if policies else None

        if not (30 <= net_days.value <= 60):
            return [Finding(
                field="payment_terms.net_days",
                status=FindingStatus.VIOLATION,
                reason=(
                    f"Net-{net_days.value} is outside the accepted Net-30 to "
                    f"Net-60 window."
                ),
                policy_id=policy_id,
                extracted_value=str(net_days.value),
            )]

        return [Finding(
            field="payment_terms.net_days",
            status=FindingStatus.COMPLIANT,
            reason=f"Net-{net_days.value} is within the accepted payment window.",
            policy_id=policy_id,
            extracted_value=str(net_days.value),
        )]

    def _check_termination_notice(
        self, extraction: ContractExtraction
    ) -> list[Finding]:
        """Check termination notice period is within acceptable bounds.

        Args:
            extraction: The contract to check.

        Returns:
            A finding for termination_notice_days, or empty if not present.
        """
        notice = extraction.termination_notice_days
        if notice.value is None or notice.confidence < MIN_CONFIDENCE:
            return []

        policies = self._kb.search_policies(
            f"termination notice {notice.value} days", limit=1
        )
        policy_id = policies[0].policy_id if policies else None

        if notice.value < 30:
            return [Finding(
                field="termination_notice_days",
                status=FindingStatus.VIOLATION,
                reason=(
                    f"Termination notice of {notice.value} days is below the "
                    f"30-day minimum."
                ),
                policy_id=policy_id,
                extracted_value=str(notice.value),
            )]
        if notice.value > 90:
            return [Finding(
                field="termination_notice_days",
                status=FindingStatus.VIOLATION,
                reason=(
                    f"Termination notice of {notice.value} days exceeds the "
                    f"90-day maximum."
                ),
                policy_id=policy_id,
                extracted_value=str(notice.value),
            )]

        return [Finding(
            field="termination_notice_days",
            status=FindingStatus.COMPLIANT,
            reason=f"Termination notice of {notice.value} days is within policy.",
            policy_id=policy_id,
            extracted_value=str(notice.value),
        )]
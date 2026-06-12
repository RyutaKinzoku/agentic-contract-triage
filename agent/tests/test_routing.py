"""Tests for the triage routing engine.

The router is pure logic (no I/O, no external dependencies), so tests are
plain unit tests with no stubs or mocks required.
"""

from app.routing import TriageOutcome, route
from app.validator import Finding, FindingStatus


def _finding(status: FindingStatus, field: str = "liability_cap") -> Finding:
    return Finding(field=field, status=status, reason="test reason")


def test_all_compliant_gives_auto_approve() -> None:
    """All compliant findings produce AUTO_APPROVE."""
    findings = [
        _finding(FindingStatus.COMPLIANT, "liability_cap"),
        _finding(FindingStatus.COMPLIANT, "governing_law"),
    ]

    result = route(findings)

    assert result.outcome is TriageOutcome.AUTO_APPROVE


def test_violation_triggers_needs_review() -> None:
    """A single VIOLATION finding is enough to route to NEEDS_REVIEW."""
    findings = [_finding(FindingStatus.VIOLATION)]

    result = route(findings)

    assert result.outcome is TriageOutcome.NEEDS_REVIEW
    assert result.reasons


def test_unknown_counterparty_triggers_needs_review() -> None:
    """An UNKNOWN_COUNTERPARTY finding routes to NEEDS_REVIEW."""
    findings = [_finding(FindingStatus.UNKNOWN_COUNTERPARTY, "counterparty.name")]

    result = route(findings)

    assert result.outcome is TriageOutcome.NEEDS_REVIEW


def test_missing_critical_field_triggers_needs_review() -> None:
    """A MISSING finding on a critical field routes to NEEDS_REVIEW."""
    findings = [_finding(FindingStatus.MISSING, "governing_law")]

    result = route(findings)

    assert result.outcome is TriageOutcome.NEEDS_REVIEW


def test_missing_non_critical_field_does_not_block_approval() -> None:
    """A MISSING finding on a non-critical field does not block AUTO_APPROVE."""
    findings = [
        _finding(FindingStatus.COMPLIANT, "liability_cap"),
        _finding(FindingStatus.MISSING, "payment_terms.net_days"),
    ]

    result = route(findings)

    assert result.outcome is TriageOutcome.AUTO_APPROVE


def test_low_confidence_on_critical_field_triggers_review() -> None:
    """LOW_CONFIDENCE on a critical field routes to NEEDS_REVIEW."""
    findings = [_finding(FindingStatus.LOW_CONFIDENCE, "liability_cap")]

    result = route(findings)

    assert result.outcome is TriageOutcome.NEEDS_REVIEW


def test_low_confidence_on_non_critical_field_allows_approval() -> None:
    """LOW_CONFIDENCE on a non-critical field does not block AUTO_APPROVE."""
    findings = [
        _finding(FindingStatus.COMPLIANT, "governing_law"),
        _finding(FindingStatus.LOW_CONFIDENCE, "confidentiality_duration"),
    ]

    result = route(findings)

    assert result.outcome is TriageOutcome.AUTO_APPROVE


def test_quarantine_flag_overrides_everything() -> None:
    """quarantine=True produces QUARANTINE regardless of findings."""
    findings = [_finding(FindingStatus.COMPLIANT)]

    result = route(findings, quarantine=True)

    assert result.outcome is TriageOutcome.QUARANTINE


def test_reasons_populated_for_needs_review() -> None:
    """NEEDS_REVIEW results carry the specific reasons from the findings."""
    reason_text = "Liability cap is unlimited"
    findings = [Finding(
        field="liability_cap",
        status=FindingStatus.VIOLATION,
        reason=reason_text,
    )]

    result = route(findings)

    assert any(reason_text in r for r in result.reasons)


def test_multiple_violations_all_listed() -> None:
    """All violation reasons appear in a NEEDS_REVIEW result."""
    findings = [
        Finding(field="liability_cap", status=FindingStatus.VIOLATION, reason="Bad cap"),
        Finding(field="governing_law", status=FindingStatus.VIOLATION, reason="Bad law"),
    ]

    result = route(findings)

    assert len(result.reasons) == 2
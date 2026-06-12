"""Tests for the contract extraction schema."""

import pytest
from pydantic import ValidationError

from app.schemas import (
    ContractExtraction,
    ContractType,
    ExtractedField,
    RenewalType,
)


def test_confidence_must_be_within_range() -> None:
    """A confidence above 1.0 is rejected by validation.

    Inputs: an ExtractedField with confidence 1.5.
    Expected: pydantic raises ValidationError.
    """
    with pytest.raises(ValidationError):
        ExtractedField[str](value="x", confidence=1.5)


def test_field_defaults_to_empty() -> None:
    """An ExtractedField defaults to a null value with zero confidence.

    Inputs: an ExtractedField constructed with no arguments.
    Expected: value is None, confidence is 0.0, source_snippet is None.
    """
    field: ExtractedField[str] = ExtractedField()
    assert field.value is None
    assert field.confidence == 0.0
    assert field.source_snippet is None


def test_parses_representative_contract() -> None:
    """A realistic extraction payload parses into the domain model.

    Inputs: a nested dict resembling a real Gemini extraction, including ISO
        dates and enum string values.
    Expected: the dict validates and date/enum coercion works as intended.
    """
    payload = {
        "counterparty": {
            "name": {
                "value": "Acme Corporation Ltd",
                "confidence": 0.97,
                "source_snippet": "...between Acme Corporation Ltd...",
            },
            "address": {
                "value": "1 Market St, London",
                "confidence": 0.8,
                "source_snippet": "registered at 1 Market St",
            },
        },
        "contract_type": {
            "value": "nda",
            "confidence": 0.95,
            "source_snippet": "Non-Disclosure Agreement",
        },
        "effective_date": {
            "value": "2026-01-15",
            "confidence": 0.9,
            "source_snippet": "effective 15 January 2026",
        },
        "term_length": {
            "value": "12 months",
            "confidence": 0.85,
            "source_snippet": "for a term of 12 months",
        },
        "end_date": {"value": "2027-01-14", "confidence": 0.6, "source_snippet": None},
        "renewal": {
            "type": {
                "value": "auto",
                "confidence": 0.7,
                "source_snippet": "shall automatically renew",
            },
            "notice_period_days": {
                "value": 30,
                "confidence": 0.7,
                "source_snippet": "30 days' notice",
            },
        },
        "payment_terms": {
            "amount": {"value": 50000.0, "confidence": 0.5, "source_snippet": None},
            "currency": {"value": "GBP", "confidence": 0.5, "source_snippet": None},
            "schedule": {"value": "monthly", "confidence": 0.4, "source_snippet": None},
            "net_days": {"value": 30, "confidence": 0.6, "source_snippet": "Net 30"},
        },
        "liability_cap": {
            "value": "12 months fees",
            "confidence": 0.8,
            "source_snippet": "liability capped at...",
        },
        "governing_law": {
            "value": "England and Wales",
            "confidence": 0.92,
            "source_snippet": "governed by the laws of England and Wales",
        },
        "termination_notice_days": {
            "value": 60,
            "confidence": 0.75,
            "source_snippet": "60 days' written notice",
        },
        "confidentiality_duration": {
            "value": "3 years",
            "confidence": 0.7,
            "source_snippet": "for a period of three years",
        },
        "signatories": {
            "value": ["Jane Doe", "John Smith"],
            "confidence": 0.6,
            "source_snippet": None,
        },
    }

    contract = ContractExtraction.model_validate(payload)

    assert contract.counterparty.name.value == "Acme Corporation Ltd"
    assert contract.contract_type.value is ContractType.NDA
    assert contract.renewal.type.value is RenewalType.AUTO
    assert contract.effective_date.value is not None
    assert contract.effective_date.value.year == 2026
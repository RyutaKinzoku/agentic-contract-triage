"""Tests for the contract extraction schema."""

from app.schemas import (
    ContractExtraction,
    ContractType,
    ExtractedField,
    RenewalType,
)


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
    assert contract.effective_date.value == "2026-01-15"


def test_confidence_percentage_is_normalised() -> None:
    """A confidence given as a percentage (95) is normalised to 0.95.

    Inputs: an ExtractedField with confidence 95.
    Expected: confidence is coerced to 0.95 rather than rejected.
    """
    field: ExtractedField[str] = ExtractedField(value="x", confidence=95)
    assert field.confidence == 0.95


def test_confidence_above_range_is_clamped() -> None:
    """A confidence slightly above 1.0 is clamped, not rejected.

    Inputs: an ExtractedField with confidence 1.4.
    Expected: confidence is clamped to 1.0.
    """
    field: ExtractedField[str] = ExtractedField(value="x", confidence=1.4)
    assert field.confidence == 1.0


def test_enum_tolerates_case_variants() -> None:
    """Uppercase or spaced enum labels resolve instead of failing.

    Inputs: contract_type 'NDA' and renewal type 'Auto'.
    Expected: they resolve to the correct enum members.
    """
    payload = {
        "contract_type": {"value": "NDA", "confidence": 0.9},
        "renewal": {"type": {"value": "Auto", "confidence": 0.9}},
    }
    contract = ContractExtraction.model_validate(payload)
    assert contract.contract_type.value is ContractType.NDA
    assert contract.renewal.type.value is RenewalType.AUTO


def test_non_iso_date_is_accepted() -> None:
    """A natural-language date is stored as-is rather than rejected.

    Inputs: effective_date '15 January 2026'.
    Expected: the value is kept verbatim.
    """
    payload = {"effective_date": {"value": "15 January 2026", "confidence": 0.8}}
    contract = ContractExtraction.model_validate(payload)
    assert contract.effective_date.value == "15 January 2026"


def test_textual_numbers_are_coerced() -> None:
    """Numbers embedded in text are parsed to ints.

    Inputs: net_days 'Net-30' and renewal notice 'ninety (90) days'.
    Expected: they become 30 and 90.
    """
    payload = {
        "payment_terms": {"net_days": {"value": "Net-30", "confidence": 0.7}},
        "renewal": {
            "notice_period_days": {"value": "ninety (90) days", "confidence": 0.7}
        },
    }
    contract = ContractExtraction.model_validate(payload)
    assert contract.payment_terms.net_days.value == 30
    assert contract.renewal.notice_period_days.value == 90


def test_signatories_objects_are_flattened() -> None:
    """Signatories returned as objects are flattened to strings.

    Inputs: signatories as a list of {name, title} dicts.
    Expected: each becomes a single string.
    """
    payload = {
        "signatories": {
            "value": [{"name": "Jane Doe", "title": "Director"}],
            "confidence": 0.6,
        }
    }
    contract = ContractExtraction.model_validate(payload)
    assert contract.signatories.value == ["Jane Doe, Director"]
"""Tests for the contract validator.

All tests use an in-memory Qdrant store with the real KB files and a deterministic
stub embedder — no network calls, no live model, but real storage and real retrieval.
"""

import hashlib

from qdrant_client import QdrantClient

from app.kb_ingest import ingest, load_clients, load_policies
from app.knowledge_base import KnowledgeBase
from app.schemas import (
    ContractExtraction,
    ContractType,
    Counterparty,
    ExtractedField,
    RenewalTerms,
    RenewalType,
)
from app.validator import ContractValidator, FindingStatus

_DIM = 16


class _StubEmbeddings:
    @property
    def dimension(self) -> int:
        return _DIM

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.strip().encode()).digest()
        return [b / 255.0 for b in digest[:_DIM]]


def _make_kb(threshold: float = 0.99) -> KnowledgeBase:
    from app.vectorstore import QdrantVectorStore
    emb = _StubEmbeddings()
    store = QdrantVectorStore(QdrantClient(":memory:"), "kb_test")
    ingest(emb, store, load_policies(), load_clients())
    return KnowledgeBase(emb, store, known_client_threshold=threshold)


def _compliant_extraction() -> ContractExtraction:
    """A fully populated, policy-compliant contract."""
    return ContractExtraction(
        counterparty=Counterparty(
            name=ExtractedField(value="Acme Corporation Ltd", confidence=0.95)
        ),
        contract_type=ExtractedField(value=ContractType.NDA, confidence=0.95),
        effective_date=ExtractedField(value="2026-01-15", confidence=0.9),
        governing_law=ExtractedField(value="England and Wales", confidence=0.92),
        liability_cap=ExtractedField(value="12 months fees", confidence=0.85),
        renewal=RenewalTerms(
            type=ExtractedField(value=RenewalType.AUTO, confidence=0.8),
            notice_period_days=ExtractedField(value=30, confidence=0.8),
        ),
    )


def test_compliant_contract_has_no_violations() -> None:
    """A fully compliant contract produces no violation findings."""
    validator = ContractValidator(_make_kb())

    findings = validator.validate(_compliant_extraction())

    statuses = {f.status for f in findings}
    assert FindingStatus.VIOLATION not in statuses
    assert FindingStatus.UNKNOWN_COUNTERPARTY not in statuses


def test_unknown_counterparty_is_flagged() -> None:
    """A counterparty not in the register produces an UNKNOWN_COUNTERPARTY finding."""
    validator = ContractValidator(_make_kb())
    extraction = _compliant_extraction()
    extraction.counterparty.name.value = "Unknown Corp Ltd"
    # Rebuild with the modified value since ExtractedField is not frozen
    extraction = ContractExtraction(
        counterparty=Counterparty(
            name=ExtractedField(value="Unknown Corp Ltd", confidence=0.95)
        ),
        contract_type=extraction.contract_type,
        effective_date=extraction.effective_date,
        governing_law=extraction.governing_law,
        liability_cap=extraction.liability_cap,
    )

    findings = validator.validate(extraction)

    counterparty_findings = [f for f in findings if f.field == "counterparty.name"]
    assert any(f.status is FindingStatus.UNKNOWN_COUNTERPARTY for f in counterparty_findings)


def test_unlimited_liability_is_a_violation() -> None:
    """'Unlimited liability' in the cap field produces a VIOLATION finding."""
    validator = ContractValidator(_make_kb())
    extraction = ContractExtraction(
        counterparty=Counterparty(
            name=ExtractedField(value="Acme Corporation Ltd", confidence=0.95)
        ),
        contract_type=ExtractedField(value=ContractType.NDA, confidence=0.9),
        effective_date=ExtractedField(value="2026-01-15", confidence=0.9),
        governing_law=ExtractedField(value="England and Wales", confidence=0.9),
        liability_cap=ExtractedField(value="unlimited", confidence=0.9),
    )

    findings = validator.validate(extraction)

    liability_findings = [f for f in findings if f.field == "liability_cap"]
    assert any(f.status is FindingStatus.VIOLATION for f in liability_findings)


def test_unapproved_jurisdiction_is_a_violation() -> None:
    """A governing law outside the approved list produces a VIOLATION finding."""
    validator = ContractValidator(_make_kb())
    extraction = ContractExtraction(
        counterparty=Counterparty(
            name=ExtractedField(value="Acme Corporation Ltd", confidence=0.95)
        ),
        contract_type=ExtractedField(value=ContractType.NDA, confidence=0.9),
        effective_date=ExtractedField(value="2026-01-15", confidence=0.9),
        governing_law=ExtractedField(value="New South Wales, Australia", confidence=0.9),
        liability_cap=ExtractedField(value="12 months fees", confidence=0.9),
    )

    findings = validator.validate(extraction)

    gov_findings = [f for f in findings if f.field == "governing_law"]
    assert any(f.status is FindingStatus.VIOLATION for f in gov_findings)


def test_missing_required_field_is_flagged() -> None:
    """A missing effective_date produces a MISSING finding."""
    validator = ContractValidator(_make_kb())
    extraction = ContractExtraction(
        counterparty=Counterparty(
            name=ExtractedField(value="Acme Corporation Ltd", confidence=0.95)
        ),
        contract_type=ExtractedField(value=ContractType.NDA, confidence=0.9),
        governing_law=ExtractedField(value="England and Wales", confidence=0.9),
        liability_cap=ExtractedField(value="12 months fees", confidence=0.9),
        # effective_date intentionally omitted
    )

    findings = validator.validate(extraction)

    date_findings = [f for f in findings if f.field == "effective_date"]
    assert any(f.status is FindingStatus.MISSING for f in date_findings)


def test_low_confidence_field_is_flagged() -> None:
    """A critical field below the confidence threshold is flagged as LOW_CONFIDENCE."""
    validator = ContractValidator(_make_kb())
    extraction = ContractExtraction(
        counterparty=Counterparty(
            name=ExtractedField(value="Acme Corporation Ltd", confidence=0.95)
        ),
        contract_type=ExtractedField(value=ContractType.NDA, confidence=0.9),
        effective_date=ExtractedField(value="2026-01-15", confidence=0.9),
        governing_law=ExtractedField(value="England and Wales", confidence=0.9),
        liability_cap=ExtractedField(value="12 months fees", confidence=0.2),
    )

    findings = validator.validate(extraction)

    cap_findings = [f for f in findings if f.field == "liability_cap"]
    assert any(f.status is FindingStatus.LOW_CONFIDENCE for f in cap_findings)


def test_auto_renewal_violation_over_60_days() -> None:
    """Auto-renewal with a notice period above 60 days is a VIOLATION."""
    validator = ContractValidator(_make_kb())
    extraction = ContractExtraction(
        counterparty=Counterparty(
            name=ExtractedField(value="Acme Corporation Ltd", confidence=0.95)
        ),
        contract_type=ExtractedField(value=ContractType.NDA, confidence=0.9),
        effective_date=ExtractedField(value="2026-01-15", confidence=0.9),
        governing_law=ExtractedField(value="England and Wales", confidence=0.9),
        liability_cap=ExtractedField(value="12 months fees", confidence=0.9),
        renewal=RenewalTerms(
            type=ExtractedField(value=RenewalType.AUTO, confidence=0.9),
            notice_period_days=ExtractedField(value=90, confidence=0.9),
        ),
    )

    findings = validator.validate(extraction)

    renewal_findings = [f for f in findings if f.field == "renewal.notice_period_days"]
    assert any(f.status is FindingStatus.VIOLATION for f in renewal_findings)
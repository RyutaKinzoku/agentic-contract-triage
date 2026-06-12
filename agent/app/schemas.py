"""Domain models for contract extraction.

These Pydantic models define the structured output the agent produces when it
reads a contract. They are the data contract shared across the pipeline: the
extraction step populates them, and the RAG and routing steps consume them.

Every extracted value is wrapped in :class:`ExtractedField`, which attaches a
confidence score and the source snippet the value was taken from. This keeps the
output auditable and gives the routing engine a real signal to decide between
automated approval and human review, rather than treating extraction as
all-or-nothing.

The ``description`` on each field doubles as guidance for the LLM: the JSON
schema generated from these models is passed to Gemini, so the field
descriptions become part of the extraction instructions.
"""

from datetime import date
from enum import Enum
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ExtractedField(BaseModel, Generic[T]):
    """A single extracted value together with provenance and confidence.

    Wrapping every field this way lets the pipeline reason about *how sure* the
    model is and *where* a value came from.

    Attributes:
        value: The extracted value, or ``None`` if it was not found.
        confidence: Confidence in ``value``, from 0.0 (guess) to 1.0 (certain).
        source_snippet: Verbatim text the value was taken from, for auditing.
    """

    value: Optional[T] = Field(
        default=None,
        description="The extracted value, or null if not present in the document.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the extracted value, from 0.0 to 1.0.",
    )
    source_snippet: Optional[str] = Field(
        default=None,
        description="Verbatim text from the document supporting the value.",
    )


class ContractType(str, Enum):
    """Recognised contract categories."""

    NDA = "nda"
    MSA = "msa"
    SOW = "sow"
    SERVICE_AGREEMENT = "service_agreement"
    OTHER = "other"


class RenewalType(str, Enum):
    """How a contract renews at the end of its term."""

    AUTO = "auto"
    MANUAL = "manual"
    NONE = "none"


class Counterparty(BaseModel):
    """The other party to the contract."""

    name: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Legal name of the counterparty.",
    )
    address: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Registered address of the counterparty.",
    )


class RenewalTerms(BaseModel):
    """Renewal configuration extracted from the contract."""

    type: ExtractedField[RenewalType] = Field(
        default_factory=ExtractedField,
        description="Whether renewal is automatic, manual, or none.",
    )
    notice_period_days: ExtractedField[int] = Field(
        default_factory=ExtractedField,
        description="Days of notice required to prevent or trigger renewal.",
    )


class PaymentTerms(BaseModel):
    """Payment configuration extracted from the contract."""

    amount: ExtractedField[float] = Field(
        default_factory=ExtractedField,
        description="Contract value or fee amount.",
    )
    currency: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="ISO currency code, e.g. USD, GBP, EUR.",
    )
    schedule: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Payment schedule, e.g. monthly, on milestones.",
    )
    net_days: ExtractedField[int] = Field(
        default_factory=ExtractedField,
        description="Payment due window in days, e.g. 30 for Net-30.",
    )


class ContractExtraction(BaseModel):
    """Structured representation of a single contract.

    This is the top-level object returned by the extraction step and consumed by
    RAG validation and the routing engine. It contains only data — no decision
    logic — so that the routing engine remains the single place where triage
    decisions are made.
    """

    counterparty: Counterparty = Field(
        default_factory=Counterparty,
        description="The other party to the agreement.",
    )
    contract_type: ExtractedField[ContractType] = Field(
        default_factory=ExtractedField,
        description="Category of the contract.",
    )
    effective_date: ExtractedField[date] = Field(
        default_factory=ExtractedField,
        description="Date the contract takes effect (ISO 8601).",
    )
    term_length: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Duration of the contract term, e.g. '12 months'.",
    )
    end_date: ExtractedField[date] = Field(
        default_factory=ExtractedField,
        description="Date the contract expires (ISO 8601).",
    )
    renewal: RenewalTerms = Field(
        default_factory=RenewalTerms,
        description="Renewal terms.",
    )
    payment_terms: PaymentTerms = Field(
        default_factory=PaymentTerms,
        description="Payment terms.",
    )
    liability_cap: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Limitation of liability, e.g. '12 months fees' or 'unlimited'.",
    )
    governing_law: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Governing law / jurisdiction, e.g. 'England and Wales'.",
    )
    termination_notice_days: ExtractedField[int] = Field(
        default_factory=ExtractedField,
        description="Notice required to terminate, in days.",
    )
    confidentiality_duration: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="How long confidentiality obligations last.",
    )
    signatories: ExtractedField[list[str]] = Field(
        default_factory=ExtractedField,
        description="Names of people who signed the contract.",
    )
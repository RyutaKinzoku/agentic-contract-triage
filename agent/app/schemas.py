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

import re
from enum import Enum
from typing import Annotated, Generic, Optional, TypeVar

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator

T = TypeVar("T")


def _coerce_int(value: object) -> Optional[int]:
    """Pull an integer out of messy model output.

    Models sometimes return numbers as words or with units, e.g. "ninety (90)
    days" or "Net-30". This extracts the first run of digits; unparseable input
    becomes None rather than failing validation.

    Args:
        value: The raw value from the model.

    Returns:
        The parsed integer, or None.
    """
    if value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value.replace(",", ""))
        return int(match.group()) if match else None
    return value


def _coerce_float(value: object) -> Optional[float]:
    """Pull a number out of messy model output (e.g. 'GBP 12,000').

    Args:
        value: The raw value from the model.

    Returns:
        The parsed float, or None.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
        return float(match.group()) if match else None
    return value


def _coerce_str_list(value: object) -> Optional[list[str]]:
    """Normalise a list of names that the model may return as objects.

    Signatories are sometimes returned as dicts ({"name": ..., "title": ...})
    or as a single string. This flattens everything to a list of strings.

    Args:
        value: The raw value from the model.

    Returns:
        A list of strings, or None.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                result.append(", ".join(str(v) for v in item.values() if v))
            else:
                result.append(str(item))
        return result
    return value


# Lenient field types: tolerate the formats models actually emit, so a stray
# "Net-30" or a signatory object never fails the whole extraction.
LenientInt = Annotated[Optional[int], BeforeValidator(_coerce_int)]
LenientFloat = Annotated[Optional[float], BeforeValidator(_coerce_float)]
LenientStrList = Annotated[Optional[list[str]], BeforeValidator(_coerce_str_list)]


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

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalise_confidence(cls, value: object) -> float:
        """Coerce a model-supplied confidence into the 0.0-1.0 range.

        Models occasionally return confidence as a percentage (e.g. 95) or
        slightly out of range. Rather than reject the whole extraction, we
        normalise: clear percentages are divided by 100 and everything is
        clamped to [0, 1].

        Args:
            value: The raw confidence from the model.

        Returns:
            A confidence in the range 0.0 to 1.0.
        """
        if value is None:
            return 0.0
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        if number > 1.0:
            # Treat clearly-percentage values (>= 2) as fractions; clamp the rest.
            number = number / 100.0 if number >= 2.0 else 1.0
        return max(0.0, min(1.0, number))


class ContractType(str, Enum):
    """Recognised contract categories."""

    NDA = "nda"
    MSA = "msa"
    SOW = "sow"
    SERVICE_AGREEMENT = "service_agreement"
    OTHER = "other"

    @classmethod
    def _missing_(cls, value: object) -> "ContractType":
        """Resolve case/format variants (e.g. 'NDA', 'Service Agreement').

        Falls back to OTHER for anything unrecognised rather than raising, so a
        stray label never fails the whole extraction.
        """
        if isinstance(value, str):
            normalised = value.strip().lower().replace("-", "_").replace(" ", "_")
            for member in cls:
                if member.value == normalised:
                    return member
        return cls.OTHER


class RenewalType(str, Enum):
    """How a contract renews at the end of its term."""

    AUTO = "auto"
    MANUAL = "manual"
    NONE = "none"

    @classmethod
    def _missing_(cls, value: object) -> "RenewalType":
        """Resolve case/format variants (e.g. 'Auto', 'AUTOMATIC').

        Falls back to NONE for anything unrecognised.
        """
        if isinstance(value, str):
            normalised = value.strip().lower()
            if normalised.startswith("auto"):
                return cls.AUTO
            if normalised.startswith("man"):
                return cls.MANUAL
            for member in cls:
                if member.value == normalised:
                    return member
        return cls.NONE


class _ExtractionModel(BaseModel):
    """Base for extraction models that tolerates null fields.

    When a field is genuinely absent, models sometimes return the whole field as
    ``null`` (e.g. ``"end_date": null``) instead of an object with a null value.
    This validator rewrites any null field to an empty object, so it becomes a
    default :class:`ExtractedField` (value None, confidence 0) rather than failing
    validation and discarding the entire extraction.
    """

    @model_validator(mode="before")
    @classmethod
    def _nulls_to_empty_fields(cls, data: object) -> object:
        """Replace any top-level null field value with an empty object.

        Args:
            data: The raw input (a dict when coming from JSON).

        Returns:
            The input with null field values replaced by empty dicts.
        """
        if isinstance(data, dict):
            return {key: ({} if value is None else value) for key, value in data.items()}
        return data


class Counterparty(_ExtractionModel):
    """The other party to the contract."""

    name: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Legal name of the counterparty.",
    )
    address: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Registered address of the counterparty.",
    )


class RenewalTerms(_ExtractionModel):
    """Renewal configuration extracted from the contract."""

    type: ExtractedField[RenewalType] = Field(
        default_factory=ExtractedField,
        description="Whether renewal is automatic, manual, or none.",
    )
    notice_period_days: ExtractedField[LenientInt] = Field(
        default_factory=ExtractedField,
        description="Days of notice required to prevent or trigger renewal.",
    )


class PaymentTerms(_ExtractionModel):
    """Payment configuration extracted from the contract."""

    amount: ExtractedField[LenientFloat] = Field(
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
    net_days: ExtractedField[LenientInt] = Field(
        default_factory=ExtractedField,
        description="Payment due window in days, e.g. 30 for Net-30.",
    )


class ContractExtraction(_ExtractionModel):
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
    effective_date: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Date the contract takes effect, ideally ISO 8601 (YYYY-MM-DD).",
    )
    term_length: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Duration of the contract term, e.g. '12 months'.",
    )
    end_date: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="Date the contract expires, ideally ISO 8601 (YYYY-MM-DD).",
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
    termination_notice_days: ExtractedField[LenientInt] = Field(
        default_factory=ExtractedField,
        description="Notice required to terminate, in days.",
    )
    confidentiality_duration: ExtractedField[str] = Field(
        default_factory=ExtractedField,
        description="How long confidentiality obligations last.",
    )
    signatories: ExtractedField[LenientStrList] = Field(
        default_factory=ExtractedField,
        description="Names of people who signed the contract.",
    )
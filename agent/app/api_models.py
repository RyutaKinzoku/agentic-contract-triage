"""HTTP response models (DTOs) for the API boundary.

The domain layer — the validator and router — uses plain dataclasses that do not
depend on the web framework. These Pydantic models adapt those domain objects to
the JSON wire format and give FastAPI an explicit, documented response schema.

Keeping the mapping here (rather than making the domain types Pydantic models)
preserves the separation: domain logic stays framework-agnostic, and the API
layer owns the contract with HTTP clients.
"""

from pydantic import BaseModel, Field

from app.routing import TriageResult
from app.schemas import ContractExtraction
from app.validator import Finding


class FindingModel(BaseModel):
    """Wire representation of a single validation finding."""

    field: str = Field(description="The contract field that was checked.")
    status: str = Field(description="Outcome of the check.")
    reason: str = Field(description="Human-readable explanation.")
    policy_id: str | None = Field(
        default=None, description="The policy rule behind the finding, if any."
    )
    extracted_value: str | None = Field(
        default=None, description="The value that was validated."
    )

    @classmethod
    def from_finding(cls, finding: Finding) -> "FindingModel":
        """Build a wire model from a domain :class:`Finding`.

        Args:
            finding: The domain finding to adapt.

        Returns:
            The corresponding wire model.
        """
        return cls(
            field=finding.field,
            status=finding.status.value,
            reason=finding.reason,
            policy_id=finding.policy_id,
            extracted_value=finding.extracted_value,
        )


class TriageResponse(BaseModel):
    """The full response from the /triage pipeline."""

    outcome: str = Field(
        description="Triage decision: auto_approve, needs_review, or quarantine."
    )
    reasons: list[str] = Field(description="Why this outcome was chosen.")
    extraction: ContractExtraction | None = Field(
        default=None,
        description="The extracted contract data, or null if extraction failed.",
    )
    findings: list[FindingModel] = Field(
        default_factory=list, description="Per-field validation findings."
    )

    @classmethod
    def build(
        cls,
        extraction: ContractExtraction | None,
        result: TriageResult,
    ) -> "TriageResponse":
        """Assemble the response from an extraction and a triage result.

        Args:
            extraction: The extracted contract, or None on quarantine.
            result: The routing decision and its findings.

        Returns:
            The assembled response model.
        """
        return cls(
            outcome=result.outcome.value,
            reasons=result.reasons,
            extraction=extraction,
            findings=[FindingModel.from_finding(f) for f in result.findings],
        )
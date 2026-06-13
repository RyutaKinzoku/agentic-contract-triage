"""Contract extraction from document attachments.

Defines the :class:`Extractor` interface and a Gemini-backed implementation.
Keeping extraction behind an interface lets the API layer depend on the
abstraction (dependency inversion) and lets tests substitute a stub instead of
calling the live model.

The Gemini implementation is multimodal: it sends the raw attachment bytes
(PDF, image, or CSV) directly to the model, so scanned and photographed
contracts are handled without a separate OCR step. The model is asked to return
JSON, which is then validated against :class:`~app.schemas.ContractExtraction`
with Pydantic, so malformed output is rejected rather than trusted.

Design note: the target schema uses a generic wrapper (``ExtractedField[T]``)
whose JSON Schema contains ``$ref``/``$defs``. Rather than rely on server-side
structured-output conversion handling that cleanly, we run the model in JSON
mode with the schema embedded in the prompt and validate locally. This is robust
to schema complexity and keeps validation firmly in our control.
"""

import asyncio
import json
import logging
from typing import Any, Protocol, runtime_checkable

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.schemas import ContractExtraction

logger = logging.getLogger(__name__)

# Computed once: the exact schema the model must conform to.
_SCHEMA_JSON = json.dumps(ContractExtraction.model_json_schema())


def _build_prompt(organisation_name: str) -> str:
    """Build the extraction prompt for a given organisation identity.

    The organisation name is woven in so the model knows which party is the
    counterparty: a contract names two parties, and "counterparty" only has
    meaning relative to whoever is running the agent.

    Args:
        organisation_name: The name of the organisation operating the agent.

    Returns:
        The full extraction prompt.
    """
    return (
        "You are a contract analyst extracting structured data from a single "
        f"contract document on behalf of {organisation_name}.\n\n"
        f"IMPORTANT: the 'counterparty' is the OTHER party to the contract — the "
        f"client, customer, vendor, or partner that {organisation_name} is "
        f"contracting with. It is never {organisation_name} itself, and never a "
        "generic role label such as 'the Provider' or 'the Company'. Extract the "
        "named external organisation as the counterparty.\n\n"
        "Return a JSON object that conforms exactly to this JSON Schema:\n\n"
        f"{_SCHEMA_JSON}\n\n"
        "For every field set:\n"
        "- `value`: the extracted value, or null if it is genuinely absent.\n"
        "- `confidence`: a number from 0.0 to 1.0 reflecting how certain you are.\n"
        "- `source_snippet`: a short verbatim quote supporting the value, or null.\n\n"
        "Do not invent or infer values that the text does not support; when unsure, "
        "lower the confidence rather than guessing. Dates should use ISO 8601 "
        "(YYYY-MM-DD) where possible. Return only the JSON object, with no "
        "surrounding prose."
    )


class ExtractionError(Exception):
    """Raised when extraction fails: a model error or unparseable output."""


@runtime_checkable
class Extractor(Protocol):
    """Interface for turning a document into a :class:`ContractExtraction`."""

    async def extract(self, content: bytes, mime_type: str) -> ContractExtraction:
        """Extract structured contract data from raw document bytes.

        Args:
            content: The raw bytes of the attachment.
            mime_type: The attachment's MIME type, e.g. 'application/pdf'.

        Returns:
            A populated :class:`ContractExtraction`.

        Raises:
            ExtractionError: If extraction or validation fails.
        """
        ...


class GeminiExtractor:
    """Extractor backed by the Gemini multimodal API.

    Args:
        api_key: Gemini API key.
        model: Gemini model identifier (e.g. 'gemini-2.5-flash').
        organisation_name: Name of the organisation running the agent, used to
            identify the counterparty during extraction.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        organisation_name: str = "our organisation",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._prompt = _build_prompt(organisation_name)

    async def extract(self, content: bytes, mime_type: str) -> ContractExtraction:
        """Extract contract data, running the blocking SDK call off the loop.

        Args:
            content: Raw attachment bytes.
            mime_type: Attachment MIME type.

        Returns:
            A validated :class:`ContractExtraction`.

        Raises:
            ExtractionError: On any model error or invalid output.
        """
        return await asyncio.to_thread(self._extract_sync, content, mime_type)

    def _extract_sync(self, content: bytes, mime_type: str) -> ContractExtraction:
        """Perform the synchronous Gemini call and validate the result.

        Args:
            content: Raw attachment bytes.
            mime_type: Attachment MIME type.

        Returns:
            A validated :class:`ContractExtraction`.

        Raises:
            ExtractionError: On any model error or invalid/unparseable output.
        """
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=[
                    types.Part.from_bytes(data=content, mime_type=mime_type),
                    self._prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                    # Extraction is a deterministic, structured task that does not
                    # benefit from extended thinking. Disabling it (Flash only)
                    # keeps the full output budget for the JSON itself, which
                    # otherwise can be exhausted by thinking tokens on the
                    # largest, fully-populated contracts — leaving an empty
                    # response that would be wrongly quarantined.
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                    max_output_tokens=8192,
                ),
            )
        # The SDK surfaces a range of provider/network errors; map them all to a
        # single domain error at this boundary so callers don't depend on SDK
        # internals.
        except Exception as exc:
            logger.exception("Gemini request failed")
            raise ExtractionError("model request failed") from exc

        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: Any) -> ContractExtraction:
        """Validate a Gemini response into the domain model.

        Args:
            response: The object returned by ``generate_content``.

        Returns:
            A validated :class:`ContractExtraction`.

        Raises:
            ExtractionError: If the response is empty or fails validation.
        """
        text = getattr(response, "text", None)
        if not text:
            # Surface *why* nothing came back (e.g. MAX_TOKENS, SAFETY) so this
            # is diagnosable from the logs rather than a silent quarantine.
            finish_reason = None
            try:
                finish_reason = response.candidates[0].finish_reason
            except (AttributeError, IndexError, TypeError):
                pass
            logger.warning(
                "Model returned an empty response (finish_reason=%s)", finish_reason
            )
            raise ExtractionError("model returned an empty response")
        try:
            return ContractExtraction.model_validate_json(text)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']} "
                f"(got {err.get('input')!r})"
                for err in exc.errors()
            )
            logger.warning("Extraction failed schema validation: %s", problems)
            raise ExtractionError("model output did not match the schema") from exc
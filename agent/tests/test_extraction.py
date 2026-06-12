"""Tests for the extraction endpoint and the GeminiExtractor error handling.

These tests never call the live model: the endpoint tests inject a stub
Extractor via dependency override, and the GeminiExtractor tests replace its
client so no network request is made.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.extraction import ExtractionError, GeminiExtractor
from app.main import create_app, get_extractor, get_settings
from app.schemas import ContractExtraction, Counterparty, ExtractedField


class _StubExtractor:
    """Returns a fixed extraction; stands in for the real model in tests."""

    def __init__(self, result: ContractExtraction) -> None:
        self._result = result

    async def extract(self, content: bytes, mime_type: str) -> ContractExtraction:
        return self._result


class _FailingExtractor:
    """Raises ExtractionError to exercise the endpoint's error path."""

    async def extract(self, content: bytes, mime_type: str) -> ContractExtraction:
        raise ExtractionError("boom")


def _build_client(extractor: object, max_mb: int = 15) -> TestClient:
    """Build a TestClient with settings and extractor dependencies overridden.

    Args:
        extractor: The stub extractor to inject.
        max_mb: Maximum attachment size for the overridden settings.

    Returns:
        A configured TestClient.
    """
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        gemini_api_key="test-key", max_attachment_mb=max_mb
    )
    app.dependency_overrides[get_extractor] = lambda: extractor
    return TestClient(app)


def _sample_extraction() -> ContractExtraction:
    """A minimal valid extraction with one populated field."""
    return ContractExtraction(
        counterparty=Counterparty(
            name=ExtractedField(value="Acme Corporation Ltd", confidence=0.95)
        )
    )


def test_rejects_unsupported_mime_type() -> None:
    """A non-allowed content type is rejected with 415."""
    client = _build_client(_StubExtractor(_sample_extraction()))

    response = client.post(
        "/extract", files={"file": ("c.txt", b"hello", "text/plain")}
    )

    assert response.status_code == 415


def test_rejects_empty_file() -> None:
    """An empty upload is rejected with 400."""
    client = _build_client(_StubExtractor(_sample_extraction()))

    response = client.post(
        "/extract", files={"file": ("c.pdf", b"", "application/pdf")}
    )

    assert response.status_code == 400


def test_rejects_oversized_file() -> None:
    """An attachment larger than the configured cap is rejected with 413."""
    client = _build_client(_StubExtractor(_sample_extraction()), max_mb=1)
    oversized = b"x" * (2 * 1024 * 1024)  # 2 MB, over the 1 MB cap

    response = client.post(
        "/extract", files={"file": ("c.pdf", oversized, "application/pdf")}
    )

    assert response.status_code == 413


def test_returns_extraction_on_success() -> None:
    """A supported file returns the extractor's structured output."""
    client = _build_client(_StubExtractor(_sample_extraction()))

    response = client.post(
        "/extract", files={"file": ("c.pdf", b"%PDF-1.4 data", "application/pdf")}
    )

    assert response.status_code == 200
    assert response.json()["counterparty"]["name"]["value"] == "Acme Corporation Ltd"


def test_maps_extraction_error_to_502() -> None:
    """A failure inside the extractor surfaces as 502, not a 500 stack trace."""
    client = _build_client(_FailingExtractor())

    response = client.post(
        "/extract", files={"file": ("c.pdf", b"%PDF-1.4 data", "application/pdf")}
    )

    assert response.status_code == 502


def test_gemini_extractor_wraps_client_errors() -> None:
    """A client-level exception is mapped to ExtractionError.

    Inputs: a GeminiExtractor whose client raises on generate_content.
    Expected: ExtractionError is raised, hiding the SDK-specific error.
    """
    extractor = GeminiExtractor(api_key="test-key", model="gemini-2.5-flash")

    def _raise(**kwargs: object) -> object:
        raise RuntimeError("network down")

    extractor._client = SimpleNamespace(  # type: ignore[assignment]
        models=SimpleNamespace(generate_content=_raise)
    )

    with pytest.raises(ExtractionError):
        asyncio.run(extractor.extract(b"data", "application/pdf"))


def test_parse_response_rejects_empty() -> None:
    """An empty model response is rejected with ExtractionError."""

    class _Empty:
        text = ""

    with pytest.raises(ExtractionError):
        GeminiExtractor._parse_response(_Empty())
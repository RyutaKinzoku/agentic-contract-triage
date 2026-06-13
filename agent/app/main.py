"""FastAPI application for the Contract Intake & Triage Agent.

Exposes the HTTP surface that n8n calls:

- ``GET  /health``  — liveness probe.
- ``POST /extract`` — extraction only (returns a ContractExtraction).
- ``POST /triage``  — the full pipeline: extract -> validate -> route.

Dependencies (settings, extractor, validator) are injected via FastAPI's
dependency system so they can be overridden in tests without touching the live
model or a running Qdrant.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from qdrant_client import QdrantClient

from app.api_models import TriageResponse
from app.config import Settings, get_settings
from app.embeddings import GeminiEmbeddings
from app.extraction import Extractor, ExtractionError, GeminiExtractor
from app.knowledge_base import KnowledgeBase
from app.routing import route
from app.schemas import ContractExtraction
from app.validator import ContractValidator
from app.vectorstore import QdrantVectorStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate configuration on startup and log non-sensitive context.

    Args:
        app: The FastAPI application instance (required by the hook; unused).

    Yields:
        Control back to the application once startup checks have passed.
    """
    settings = get_settings()
    logger.info("Agent starting with model '%s'", settings.gemini_model)
    yield
    logger.info("Agent shutting down")


# ----------------------------------------------------------------------
# Dependency providers (composition root)
# ----------------------------------------------------------------------


def get_extractor(settings: Settings = Depends(get_settings)) -> Extractor:
    """Provide the configured extractor.

    Args:
        settings: Application settings (injected).

    Returns:
        A ready-to-use Extractor.
    """
    return GeminiExtractor(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
        organisation_name=settings.organisation_name,
    )


def get_knowledge_base(
    settings: Settings = Depends(get_settings),
) -> KnowledgeBase:
    """Provide a knowledge base wired to Gemini embeddings and Qdrant.

    Args:
        settings: Application settings (injected).

    Returns:
        A KnowledgeBase querying the configured Qdrant collection.
    """
    embeddings = GeminiEmbeddings(api_key=settings.gemini_api_key.get_secret_value())
    client = QdrantClient(url=settings.qdrant_url)
    store = QdrantVectorStore(client, settings.qdrant_collection)
    return KnowledgeBase(embeddings, store)


def get_validator(
    kb: KnowledgeBase = Depends(get_knowledge_base),
) -> ContractValidator:
    """Provide the contract validator.

    Args:
        kb: The knowledge base (injected).

    Returns:
        A ContractValidator bound to the knowledge base.
    """
    return ContractValidator(kb)


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------


async def _read_validated_upload(file: UploadFile, settings: Settings) -> bytes:
    """Validate an uploaded attachment and return its raw bytes.

    Attachments arrive from email and are untrusted, so the type and size are
    checked before the content is read into memory or sent onward.

    Args:
        file: The uploaded file.
        settings: Application settings.

    Returns:
        The validated file content.

    Raises:
        HTTPException: 415 for an unsupported type, 400 for an empty file,
            or 413 for a file over the size limit.
    """
    if file.content_type not in settings.allowed_mime_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content type: {file.content_type}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    max_bytes = settings.max_attachment_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds the {settings.max_attachment_mb} MB limit.",
        )

    return content


# ----------------------------------------------------------------------
# Application
# ----------------------------------------------------------------------


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        A configured FastAPI instance with routes and lifespan registered.
    """
    app = FastAPI(
        title="Contract Intake & Triage Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Liveness probe used by Docker and n8n.

        Returns:
            A small payload indicating the service is running.
        """
        return {"status": "ok"}

    @app.post("/extract", response_model=ContractExtraction, tags=["extraction"])
    async def extract(
        file: UploadFile = File(...),
        settings: Settings = Depends(get_settings),
        extractor: Extractor = Depends(get_extractor),
    ) -> ContractExtraction:
        """Extract structured data from an uploaded contract.

        Args:
            file: The uploaded contract (PDF, image, or CSV).
            settings: Application settings (injected).
            extractor: The extraction strategy (injected).

        Returns:
            The structured :class:`ContractExtraction`.

        Raises:
            HTTPException: 415/400/413 for invalid uploads, 502 if extraction
                fails.
        """
        content = await _read_validated_upload(file, settings)
        try:
            return await extractor.extract(content, file.content_type)
        except ExtractionError as exc:
            logger.warning("Extraction failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to extract data from the document.",
            ) from exc

    @app.post("/triage", response_model=TriageResponse, tags=["triage"])
    async def triage(
        file: UploadFile = File(...),
        settings: Settings = Depends(get_settings),
        extractor: Extractor = Depends(get_extractor),
        validator: ContractValidator = Depends(get_validator),
    ) -> TriageResponse:
        """Run the full pipeline on an uploaded contract.

        Extracts the contract, validates it against the knowledge base, and
        routes it to a triage outcome. If extraction fails, the contract is
        quarantined rather than the request erroring, so the caller always
        receives a routable decision.

        Args:
            file: The uploaded contract (PDF, image, or CSV).
            settings: Application settings (injected).
            extractor: The extraction strategy (injected).
            validator: The contract validator (injected).

        Returns:
            A :class:`TriageResponse` with the outcome, reasons, extraction,
            and per-field findings.

        Raises:
            HTTPException: 415/400/413 for invalid uploads.
        """
        content = await _read_validated_upload(file, settings)

        try:
            extraction = await extractor.extract(content, file.content_type)
        except ExtractionError as exc:
            logger.warning("Extraction failed during triage; quarantining: %s", exc)
            result = route([], quarantine=True)
            return TriageResponse.build(None, result)

        # Validation queries the knowledge base (network/blocking), so run it
        # off the event loop.
        findings = await asyncio.to_thread(validator.validate, extraction)
        result = route(findings)
        return TriageResponse.build(extraction, result)

    return app


app = create_app()
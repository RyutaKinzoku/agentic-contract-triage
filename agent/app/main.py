"""FastAPI application for the Contract Intake & Triage Agent.

Exposes the HTTP surface that n8n calls: a health probe and the extraction
endpoint. Dependencies (settings and the extractor) are injected via FastAPI's
dependency system so they can be overridden in tests without touching the live
model.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status

from app.config import Settings, get_settings
from app.extraction import Extractor, ExtractionError, GeminiExtractor
from app.schemas import ContractExtraction

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate configuration on startup and log non-sensitive context.

    Loading settings here means a missing or invalid configuration surfaces the
    moment the container starts. The API key is stored as a SecretStr and is
    never logged.

    Args:
        app: The FastAPI application instance (required by the hook; unused).

    Yields:
        Control back to the application once startup checks have passed.
    """
    settings = get_settings()
    logger.info("Agent starting with model '%s'", settings.gemini_model)
    yield
    logger.info("Agent shutting down")


def get_extractor(settings: Settings = Depends(get_settings)) -> Extractor:
    """Provide the configured extractor.

    This is the composition root: the concrete Gemini implementation is wired to
    the abstract interface the endpoint depends on. Overriding this dependency in
    tests swaps in a stub with no network access.

    Args:
        settings: Application settings (injected).

    Returns:
        A ready-to-use Extractor.
    """
    return GeminiExtractor(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
    )


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

        The attachment is validated for type and size before being sent to the
        model, since it is untrusted input arriving from email.

        Args:
            file: The uploaded contract (PDF, image, or CSV).
            settings: Application settings (injected).
            extractor: The extraction strategy (injected).

        Returns:
            The structured :class:`ContractExtraction`.

        Raises:
            HTTPException: 415 if the type is unsupported, 400 if empty,
                413 if too large, or 502 if extraction fails.
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

        try:
            return await extractor.extract(content, file.content_type)
        except ExtractionError as exc:
            logger.warning("Extraction failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to extract data from the document.",
            ) from exc

    return app


app = create_app()
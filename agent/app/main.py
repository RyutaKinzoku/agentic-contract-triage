"""FastAPI application for the Contract Intake & Triage Agent.

This module exposes the HTTP surface that n8n calls. For now it provides a
health check; the extraction, RAG, and routing endpoints are added in later
commits.

The application is built with a factory (:func:`create_app`) so tests can
construct an isolated instance, and configuration is validated at startup via
the lifespan hook so a misconfigured container fails fast and loudly instead of
erroring on the first real request.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings

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

    return app


app = create_app()
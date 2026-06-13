"""Application configuration.

Settings are loaded from environment variables (and an optional ``.env`` file)
so that secrets never live in source control. The API key is stored as a
:class:`~pydantic.SecretStr` so it is not accidentally printed or logged, and it
has no default so the application fails fast if it is missing.

Access settings through :func:`get_settings`, which returns a single cached,
validated instance.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application settings sourced from the environment.

    Attributes:
        gemini_api_key: API key for Gemini. Required; no default, so a missing
            key surfaces immediately at startup.
        gemini_model: Gemini model identifier used for extraction.
        qdrant_url: Base URL of the Qdrant vector store.
        qdrant_collection: Collection holding the policy knowledge base.
        google_sheets_id: Target spreadsheet for results (optional until wired).
        google_application_credentials: Path to the Google service-account JSON.
        max_attachment_mb: Maximum accepted attachment size, in megabytes.
        allowed_mime_types: Attachment MIME types the agent will process.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: SecretStr = Field(description="API key for the Gemini API.")
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model used for extraction.",
    )

    organisation_name: str = Field(
        default="Northwind Technology Ltd",
        description=(
            "Name of the organisation running this agent. Used during extraction "
            "to identify the counterparty (the other party to the contract)."
        ),
    )

    qdrant_url: str = Field(
        default="http://qdrant:6333",
        description="Qdrant base URL.",
    )
    qdrant_collection: str = Field(
        default="contract_kb",
        description="Knowledge-base collection name.",
    )

    google_sheets_id: str | None = Field(
        default=None,
        description="Target Google Sheet ID.",
    )
    google_application_credentials: str | None = Field(
        default=None,
        description="Path to the Google service-account JSON file.",
    )

    max_attachment_mb: int = Field(
        default=15,
        ge=1,
        description="Maximum attachment size in MB. Larger files are rejected.",
    )
    allowed_mime_types: list[str] = Field(
        default=[
            "application/pdf",
            "image/png",
            "image/jpeg",
            "text/csv",
        ],
        description="Attachment MIME types the agent will accept.",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings.

    Returns:
        A single, validated :class:`Settings` instance shared across the app.
    """
    return Settings()
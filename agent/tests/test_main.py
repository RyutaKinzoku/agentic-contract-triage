"""Tests for the FastAPI application."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ok() -> None:
    """The health endpoint returns 200 with an ok status.

    Inputs: a GET request to /health.
    Expected: HTTP 200 and body {"status": "ok"}.
    """
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
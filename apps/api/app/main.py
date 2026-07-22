"""ASGI entrypoint kept intentionally small for Uvicorn and tests."""

from app.bootstrap.application import create_app


app = create_app()

"""FastAPI application entry point."""

from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="AI accounts-payable automation agent (prototype)",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    """Liveness check used by Docker, CI and Render."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}

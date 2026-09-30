"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.invoices import router as invoices_router
from app.api.reviews import router as reviews_router
from app.config import get_settings
from app.db.session import init_db
from app.extraction.ocr import load_engine
from app.sap.mock import router as mock_sap_router

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_engine()  # load RapidOCR models once, not per request
    yield


app = FastAPI(
    title=settings.app_name,
    description="AI accounts-payable automation agent (prototype)",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(invoices_router)
app.include_router(reviews_router)
app.include_router(mock_sap_router)


@app.get("/health")
def health() -> dict:
    """Liveness check used by Docker, CI and Render."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}

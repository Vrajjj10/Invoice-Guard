"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app.api.invoices import router as invoices_router
from app.api.reviews import router as reviews_router
from app.api.stats import router as stats_router
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
app.include_router(stats_router)
app.include_router(mock_sap_router)


INDEX_HTML = Path(__file__).parent / "static" / "index.html"
SAMPLES_DIR = Path(__file__).resolve().parents[1] / "data" / "samples"


@app.get("/", include_in_schema=False)
def root() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/samples", include_in_schema=False)
def list_samples() -> list[str]:
    """Numbered demo invoices bundled with the app (synthetic)."""
    return sorted(p.name for p in SAMPLES_DIR.glob("[0-9]_*.pdf"))


@app.get("/samples/{name}", include_in_schema=False)
def get_sample(name: str) -> FileResponse:
    if name not in list_samples():  # whitelist: no path traversal
        raise HTTPException(404, "Sample not found")
    return FileResponse(SAMPLES_DIR / name, media_type="application/pdf")


@app.get("/health")
def health() -> dict:
    """Liveness check used by Docker, CI and Render."""
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}

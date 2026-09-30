"""Database tables."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _now() -> datetime:
    return datetime.now(UTC)


class JobStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))  # pdf | png | jpg
    stored_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extraction results
    extraction_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # LLM results (Phase 3)
    fields_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    llm_escalated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    llm_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Validation / agent results (Phase 4)
    dup_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    checks_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_iterations: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

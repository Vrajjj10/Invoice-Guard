"""Typed application settings, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "InvoiceGuard"
    environment: str = "dev"

    # Secrets: never hardcoded, always from .env
    gemini_api_key: str = ""
    slack_webhook_url: str = ""

    # LLM models: cheap default, stronger model only for escalation
    default_model: str = "gemini-3.5-flash-lite"
    escalation_model: str = "gemini-3.5-flash"

    # Storage
    database_url: str = "sqlite:///./invoiceguard.db"
    upload_dir: str = "data/uploads"

    # Mock SAP endpoint (swap for a real URL later)
    sap_base_url: str = "http://localhost:8000/mock-sap"


@lru_cache
def get_settings() -> Settings:
    return Settings()

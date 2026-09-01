"""
config.py
---------
Load and validate application settings from environment variables / .env.

Supports:
  - Google AI Studio (Gemini)  → active by default
  - Azure OpenAI               → ready to enable later
  - OpenAI                     → optional
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    GOOGLE = "google"
    AZURE_OPENAI = "azure_openai"
    OPENAI = "openai"


class Settings(BaseSettings):
    """Application settings loaded from .env / environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Provider selection
    # -------------------------------------------------------------------------
    llm_provider: LLMProvider = Field(
        default=LLMProvider.GOOGLE,
        description="Active LLM provider: google | azure_openai | openai",
    )

    # -------------------------------------------------------------------------
    # Google AI Studio (Gemini)
    # -------------------------------------------------------------------------
    google_api_key: Optional[str] = Field(default=None, description="Google AI Studio API key")
    google_model: str = Field(default="gemini-2.0-flash", description="Gemini model name")

    # -------------------------------------------------------------------------
    # Azure OpenAI  (enable later – set LLM_PROVIDER=azure_openai in .env)
    # -------------------------------------------------------------------------
    azure_openai_api_key: Optional[str] = Field(default=None, description="Azure OpenAI API key")
    azure_openai_endpoint: Optional[str] = Field(default=None, description="Azure OpenAI endpoint URL")
    azure_openai_deployment: Optional[str] = Field(default=None, description="Azure OpenAI deployment name")
    azure_openai_api_version: str = Field(
        default="2024-12-01-preview",
        description="Azure OpenAI API version",
    )

    # -------------------------------------------------------------------------
    # OpenAI (optional)
    # -------------------------------------------------------------------------
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI API key")
    openai_model: str = Field(default="gpt-4o", description="OpenAI model name")

    # -------------------------------------------------------------------------
    # Generation parameters
    # -------------------------------------------------------------------------
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=256, le=128000)

    # -------------------------------------------------------------------------
    # Validation loop
    # -------------------------------------------------------------------------
    max_validation_retries: int = Field(default=2, ge=0, le=10)
    validation_pass_score: float = Field(default=0.7, ge=0.0, le=1.0)

    # -------------------------------------------------------------------------
    # Research tools
    # -------------------------------------------------------------------------
    tavily_api_key: Optional[str] = Field(default=None)
    serpapi_api_key: Optional[str] = Field(default=None)
    enable_web_search: bool = Field(default=False)
    
    # -------------------------------------------------------------------------
    # Admin bootstrap
    # -------------------------------------------------------------------------
    admin_email: str = Field(default="admin@admin.com")
    admin_password: str = Field(default="admin123")
    admin_username: str = Field(default="admin")

    # -------------------------------------------------------------------------
    # Output
    # -------------------------------------------------------------------------
    output_dir: str = Field(default="output")
    default_output_formats: str = Field(
        default="md,pdf,docx",
        description="Comma-separated formats: md, pdf, docx",
    )

    # -------------------------------------------------------------------------
    # Application
    # -------------------------------------------------------------------------
    log_level: str = Field(default="INFO")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000, ge=1, le=65535)
    request_timeout_seconds: int = Field(default=300, ge=30, le=3600)

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------
    @field_validator("llm_provider", mode="before")
    @classmethod
    def normalize_provider(cls, v):
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, v):
        if isinstance(v, str):
            return v.strip().upper()
        return v

    @model_validator(mode="after")
    def validate_provider_credentials(self) -> "Settings":
        """Ensure the selected provider has the required credentials."""
        provider = self.llm_provider

        if provider == LLMProvider.GOOGLE:
            if not self.google_api_key or self.google_api_key.startswith("your_"):
                raise ValueError(
                    "GOOGLE_API_KEY is required when LLM_PROVIDER=google. "
                    "Get a free key at https://aistudio.google.com/apikey"
                )

        elif provider == LLMProvider.AZURE_OPENAI:
            missing = []
            if not self.azure_openai_api_key:
                missing.append("AZURE_OPENAI_API_KEY")
            if not self.azure_openai_endpoint:
                missing.append("AZURE_OPENAI_ENDPOINT")
            if not self.azure_openai_deployment:
                missing.append("AZURE_OPENAI_DEPLOYMENT")
            if missing:
                raise ValueError(
                    "Azure OpenAI selected but missing: " + ", ".join(missing)
                )

        elif provider == LLMProvider.OPENAI:
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")

        return self

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------
    @property
    def output_formats_list(self) -> List[str]:
        """Parse DEFAULT_OUTPUT_FORMATS into a clean list."""
        return [f.strip().lower() for f in self.default_output_formats.split(",") if f.strip()]

    @property
    def active_model_name(self) -> str:
        """Return the model/deployment name for the active provider."""
        if self.llm_provider == LLMProvider.GOOGLE:
            return self.google_model
        if self.llm_provider == LLMProvider.AZURE_OPENAI:
            return self.azure_openai_deployment or "unknown"
        return self.openai_model

    def ensure_output_dir(self) -> str:
        """Create the output directory if it does not exist. Returns the path."""
        os.makedirs(self.output_dir, exist_ok=True)
        return self.output_dir


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings singleton.
    Call get_settings.cache_clear() in tests if you change env vars.
    """
    return Settings()


# Convenience: importable default instance
# (will raise on import if required keys are missing — that is intentional)
settings = get_settings()
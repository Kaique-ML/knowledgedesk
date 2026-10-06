from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KD_", env_file=".env", extra="ignore")

    environment: Literal["local", "deployment"] = "local"
    mode: Literal["search", "demo", "rag"] = "search"
    database_url: str = (
        "postgresql+psycopg://knowledgedesk:local-only@127.0.0.1:55432/knowledgedesk"
    )
    secret_key: str | None = None
    storage_root: Path = Path("data/private")
    max_upload_bytes: int = 20 * 1024 * 1024
    max_archive_bytes: int = 100 * 1024 * 1024
    max_image_pixels: int = 40_000_000
    max_pdf_pages: int = 250
    ollama_url: str = "http://127.0.0.1:11434"
    embedding_model: str | None = None
    generation_model: str | None = None
    embedding_dimensions: int | None = None
    context_characters: int = 16_000
    generation_context_tokens: int = 4_096
    generation_max_tokens: int = 512
    generation_timeout_seconds: int = 300
    question_rate_limit_per_minute: int = 20
    retrieval_top_k: int = 20
    response_top_k: int = 6
    session_seconds: int = 28_800
    secure_cookies: bool = False

    @field_validator("embedding_model", "generation_model", "embedding_dimensions", mode="before")
    @classmethod
    def empty_optional_values_are_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_deployment(self) -> Settings:
        if self.environment == "deployment":
            if not self.secret_key or len(self.secret_key) < 32:
                raise ValueError(
                    "KD_SECRET_KEY of at least 32 characters is required in deployment"
                )
            if not self.secure_cookies:
                raise ValueError("KD_SECURE_COOKIES must be enabled in deployment")
        if self.max_upload_bytes < 1 or self.max_archive_bytes < self.max_upload_bytes:
            raise ValueError(
                "upload limits must be positive and archive limit must cover upload limit"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

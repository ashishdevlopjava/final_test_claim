from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///./claims.db"
    log_level: str = "INFO"
    api_key: str | None = None
    llm_provider: str = "mock"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_deployment: str | None = None
    llm_timeout_seconds: float = Field(default=15, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    review_amount_threshold: float = Field(default=5000, ge=0)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def resolved_llm_api_key(self) -> str | None:
        return self.azure_openai_api_key or self.llm_api_key

    @property
    def resolved_llm_base_url(self) -> str | None:
        if self.llm_provider == "azure_openai" and self.azure_openai_endpoint:
            return f"{self.azure_openai_endpoint.rstrip('/')}/openai/v1/"
        return self.llm_base_url

    @property
    def resolved_llm_model(self) -> str:
        if self.llm_provider == "azure_openai" and self.azure_openai_deployment:
            return self.azure_openai_deployment
        return self.llm_model


@lru_cache
def get_settings() -> Settings:
    return Settings()

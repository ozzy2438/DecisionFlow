"""Application configuration loaded only from explicit settings or environment variables."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """DecisionFlow runtime settings.

    Secrets are deliberately absent from defaults. The local database password is only for the
    isolated Docker development network and is overridden in deployed environments.
    """

    model_config = SettingsConfigDict(
        env_prefix="DECISIONFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development")
    database_url: str = Field(
        default="postgresql+psycopg://decisionflow:decisionflow@localhost:5432/decisionflow"
    )
    log_level: str = Field(default="INFO")
    mlflow_tracking_uri: str = Field(default="http://localhost:5050")
    mlflow_experiment: str = Field(default="decisionflow-sla-risk")
    mlflow_model_name: str = Field(default="decisionflow-sla-risk")
    mlflow_model_alias: str = Field(default="candidate")
    llm_provider: str = Field(default="template")
    llm_model: str = Field(default="gpt-5.6")
    openai_api_key: SecretStr | None = Field(default=None)
    temporal_address: str = Field(default="localhost:7233")
    temporal_namespace: str = Field(default="default")
    temporal_task_queue: str = Field(default="decisionflow-p2p")
    orchestration_mode: str = Field(default="temporal")
    auth_tokens_json: str = Field(default="{}")
    cors_origins: str = Field(default="http://localhost:3000")

    @property
    def psycopg_dsn(self) -> str:
        """Return a DSN understood by psycopg rather than SQLAlchemy."""

        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()

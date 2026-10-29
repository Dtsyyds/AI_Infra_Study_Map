from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARC_", env_file=".env", extra="ignore")
    database_url: SecretStr = SecretStr("postgresql://arc_ref@127.0.0.1:55439/arc_reference")
    user_token: SecretStr = SecretStr("local-demo-reviewer-not-a-secret")
    restricted_token: SecretStr = SecretStr("local-demo-restricted-not-a-secret")
    ops_token: SecretStr = SecretStr("local-demo-operator-not-a-secret")
    business_url: str = "http://127.0.0.1:18082"
    docker_image: str = "python:3.11.17-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf"
    lease_seconds: int = 8
    test_mode: bool = False
    test_barrier: str = ""
    api_port: int = 18081
    business_port: int = 18082

    model_temperature: float = Field(default=0, ge=0, le=2)
    model_enabled: bool = False
    model_name: str = ""
    model_secret_env: str = Field(default="ARC_APPROVED_MODEL_KEY", pattern=r"^[A-Z][A-Z0-9_]+$")
    model_budget_id: str = ""
    model_request_limit: int = Field(default=0, ge=0, le=1000)
    model_token_limit: int = Field(default=0, ge=0)
    model_max_output_tokens: int = Field(default=2048, ge=128, le=8192)
    model_task_request_limit: int = Field(default=3, ge=1, le=3)


settings = Settings()

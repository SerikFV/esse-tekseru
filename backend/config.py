from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = Field(default="gemini-2.5-flash", pattern=r"^[a-zA-Z0-9.-]+$")
    gemini_timeout_seconds: float = Field(default=90, ge=1, le=180)
    database_url: SecretStr = SecretStr("")

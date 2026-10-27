from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = Field(default="gemini-3.5-flash-lite", pattern=r"^[a-zA-Z0-9.-]+$")
    gemini_timeout_seconds: float = Field(default=90, ge=1, le=180)
    database_url: SecretStr = SecretStr("")
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_email: str = ""
    smtp_from_name: str = "tekSER"
    smtp_ssl: bool = False
    cookie_secure: bool = False
    session_days: int = Field(default=7, ge=1, le=30)
    site_url: str = ""
    bootstrap_admin_email: str = ""

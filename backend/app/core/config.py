"""Application configuration — env-driven, fails loudly on missing secrets (R14)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore", populate_by_name=True)

    # Environment: local | staging | production
    env: str = "local"
    debug: bool = False

    # Database / cache
    database_url: str = "postgresql+psycopg2://liger_user:liger_8010@localhost:5433/liger_production"
    redis_url: str = "redis://localhost:6379/0"

    # Auth — no default secret outside local (validated below)
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # OTP (BR-AC-09)
    otp_length: int = 6
    otp_ttl_minutes: int = 5
    otp_max_per_hour: int = 5
    otp_max_attempts: int = 5

    # CORS — comma-separated in env, e.g. "http://localhost:3000,https://liger.in"
    cors_origins_raw: str = Field(default="http://localhost:3000", alias="cors_origins")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    # Business timezone for display / schedules
    business_tz: str = "Asia/Kolkata"

    # Object storage (T-EXT; empty locally until MinIO/S3 configured)
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    cdn_base_url: str = ""

    def model_post_init(self, __context: object) -> None:
        if self.env == "local" and not self.jwt_secret:
            # Deterministic local-only secret; production MUST set JWT_SECRET (R14).
            self.jwt_secret = "liger-local-dev-secret-not-for-production"
        if self.env != "local" and not self.jwt_secret:
            raise RuntimeError("JWT_SECRET is required outside local (R14)")


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()

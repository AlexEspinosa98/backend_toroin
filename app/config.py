from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "sqlite:///./dev.db"
    jwt_secret: str = "dev-secret-cambiar"
    jwt_expire_min: int = 480
    superadmin_email: str = "admin@tuplataforma.com"
    superadmin_password: str = "cambiar-ya"
    cors_origins: str = "http://localhost:3000"

    @property
    def db_url(self) -> str:
        """Normaliza la URL para el driver psycopg 3 (Supabase/Neon entregan postgresql://)."""
        u = self.database_url
        if u.startswith("postgres://"):
            u = "postgresql://" + u[len("postgres://"):]
        if u.startswith("postgresql://"):
            u = "postgresql+psycopg://" + u[len("postgresql://"):]
        return u

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.env == "prod" and s.jwt_secret.startswith(("dev-secret", "cambia")):
        raise RuntimeError("JWT_SECRET inseguro en producción")
    return s

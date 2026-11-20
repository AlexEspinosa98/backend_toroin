import os
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


def _limpiar_query(u: str) -> str:
    """Quita parámetros que libpq no entiende (p. ej. `supa=` que agrega Supabase)."""
    if u.startswith("sqlite"):
        return u
    p = urlsplit(u)
    q = [(k, v) for k, v in parse_qsl(p.query) if k != "supa"]
    return urlunsplit(p._replace(query=urlencode(q)))


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
        # Integración Supabase↔Vercel: si no hay DATABASE_URL explícita, usar POSTGRES_URL (pooler IPv4)
        if u.startswith("sqlite") and "DATABASE_URL" not in os.environ and os.environ.get("POSTGRES_URL"):
            u = os.environ["POSTGRES_URL"]
        if u.startswith("postgres://"):
            u = "postgresql://" + u[len("postgres://"):]
        if u.startswith("postgresql://"):
            u = "postgresql+psycopg://" + u[len("postgresql://"):]
        return _limpiar_query(u)

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.env == "prod" and s.jwt_secret.startswith(("dev-secret", "cambia")):
        raise RuntimeError("JWT_SECRET inseguro en producción")
    return s

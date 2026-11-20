from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import auth, empresa, negocio, platform, public

settings = get_settings()

app = FastAPI(
    title="TOROIN Plataforma API",
    description="API multiempresa (marca blanca) con licencias y módulos activables.",
    version="0.1.0",
    docs_url="/docs" if settings.env != "prod" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.env != "prod" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["sistema"])
def health():
    return {"ok": True}


@app.get("/health/db", tags=["sistema"])
def health_db():
    """Diagnóstico de conexión (sin secretos). Quitar cuando el despliegue esté estable."""
    import re

    from sqlalchemy import inspect, text

    from .db import engine

    import os
    import socket

    from sqlalchemy.engine import make_url

    from .config import get_settings

    u = make_url(get_settings().db_url)
    info = {"host": u.host, "port": u.port, "usuario": (u.username or "")[:24],
            "origen": "DATABASE_URL" if "DATABASE_URL" in os.environ else ("POSTGRES_URL" if os.environ.get("POSTGRES_URL") else "default")}
    try:
        info["dns_v4"] = socket.getaddrinfo(u.host, u.port or 5432, socket.AF_INET)[0][4][0]
    except Exception as e:  # noqa: BLE001
        info["dns_v4"] = f"ERROR {type(e).__name__}: {e}"
    try:
        info["dns_v6"] = socket.getaddrinfo(u.host, u.port or 5432, socket.AF_INET6)[0][4][0]
    except Exception as e:  # noqa: BLE001
        info["dns_v6"] = f"ERROR {type(e).__name__}: {e}"
    try:
        with engine.connect() as c:
            c.execute(text("select 1"))
            tablas = sorted(inspect(c).get_table_names())
        return {**info, "conexion": "ok", "tablas": len(tablas), "tiene_empresas": "empresas" in tablas}
    except Exception as e:  # noqa: BLE001
        msg = re.sub(r"(://[^:/@\s]+:)[^@\s]+@", r"\1***@", str(e))
        return {**info, "conexion": "error", "tipo": type(e).__name__, "detalle": msg[:300]}


for r in (public.router, auth.router, platform.router, empresa.router, negocio.router):
    app.include_router(r)

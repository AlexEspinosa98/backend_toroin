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


for r in (public.router, auth.router, platform.router, empresa.router, negocio.router):
    app.include_router(r)

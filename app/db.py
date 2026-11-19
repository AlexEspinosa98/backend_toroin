from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings

_s = get_settings()
_url = _s.db_url

if _url.startswith("sqlite"):
    engine = create_engine(_url, connect_args={"check_same_thread": False})
else:
    # Serverless (Vercel) + pooler de Supabase en modo transacción:
    #  - NullPool: cada invocación abre/cierra; el pooler del proveedor reparte las conexiones.
    #  - prepare_threshold=None: el pooler transaccional no soporta prepared statements.
    engine = create_engine(_url, poolclass=NullPool, connect_args={"prepare_threshold": None})

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()

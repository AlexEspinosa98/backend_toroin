import socket
import time

import psycopg
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
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
    _u = make_url(_url)

    def _ipv4(host: str, port: int) -> str | None:
        # En Vercel la resolución DNS a veces falla con EBUSY (Errno 16) y Supabase directo es solo IPv6:
        # resolvemos nosotros a IPv4 con reintentos y pasamos hostaddr (host se conserva para TLS).
        for i in range(4):
            try:
                return socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
            except OSError:
                time.sleep(0.15 * (i + 1))
        return None

    def _conectar():
        kw = dict(host=_u.host, port=_u.port or 5432, dbname=_u.database, user=_u.username,
                  password=_u.password, prepare_threshold=None, connect_timeout=10)
        kw.update({k: v for k, v in _u.query.items()})
        ip = _ipv4(kw["host"], kw["port"])
        if ip:
            kw["hostaddr"] = ip
        return psycopg.connect(**kw)

    engine = create_engine(_url, poolclass=NullPool, creator=_conectar)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()

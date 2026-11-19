from fastapi import Request
from sqlalchemy.orm import Session

from .models import Auditoria


def registrar(
    db: Session,
    *,
    accion: str,
    usuario=None,
    empresa_id: str | None = None,
    entidad: str = "",
    entidad_id: str = "",
    detalle: dict | None = None,
    request: Request | None = None,
) -> None:
    db.add(
        Auditoria(
            empresa_id=empresa_id,
            usuario_id=getattr(usuario, "id", None),
            usuario_correo=getattr(usuario, "correo", ""),
            accion=accion,
            entidad=entidad,
            entidad_id=entidad_id,
            detalle=detalle or {},
            ip=(request.client.host if request and request.client else ""),
        )
    )

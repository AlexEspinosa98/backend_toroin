from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Empresa
from .auth import _marca

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/empresas/{slug}/marca")
def marca(slug: str, db: Session = Depends(get_db)):
    """Marca pública de la empresa: se usa en la pantalla de login, antes de autenticarse."""
    e = db.scalars(select(Empresa).where(Empresa.slug == slug.lower())).first()
    if not e or e.estado == "cancelada":
        raise HTTPException(404, "Empresa no encontrada")
    return _marca(e)

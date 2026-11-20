from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import catalog
from ..audit import registrar
from ..db import get_db
from ..deps import Sesion, modulos_de_usuario, sesion_actual
from ..licensing import resolver
from ..models import Empresa, Usuario
from ..schemas import CambiarPasswordIn, LoginIn
from ..security import crear_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash falso para igualar el tiempo de respuesta cuando el usuario no existe
_FALSO = hash_password("no-existe")


@router.post("/login")
def login(datos: LoginIn, request: Request, db: Session = Depends(get_db)):
    empresa = None
    if datos.empresa:
        empresa = db.scalars(select(Empresa).where(Empresa.slug == datos.empresa.strip().lower())).first()
        if not empresa:
            verify_password(datos.password, _FALSO)
            raise HTTPException(401, "Credenciales inválidas")
    q = select(Usuario).where(
        Usuario.correo == datos.correo.lower(),
        Usuario.empresa_id == (empresa.id if empresa else None),
    )
    u = db.scalars(q).first()
    if not u or not u.activo or not verify_password(datos.password, u.password_hash):
        if not u:
            verify_password(datos.password, _FALSO)
        raise HTTPException(401, "Credenciales inválidas")
    if empresa:
        est = resolver(db, empresa)
        if not est.lectura:
            raise HTTPException(403, est.mensaje or "Cuenta sin acceso")
    u.ultimo_acceso = datetime.now(timezone.utc)
    registrar(db, accion="login", usuario=u, empresa_id=u.empresa_id, request=request)
    db.commit()
    return {
        "token": crear_token(u.id, u.rol, u.empresa_id),
        "debe_cambiar_password": u.debe_cambiar_password,
    }


def _marca(e: Empresa) -> dict:
    return {
        "slug": e.slug, "nombre": e.nombre, "nombre_app": e.nombre_app or e.nombre,
        "color_primario": e.color_primario, "color_secundario": e.color_secundario,
        "logo_claro": e.logo_claro, "logo_oscuro": e.logo_oscuro, "icono": e.icono, "pie": e.pie,
    }


@router.get("/me")
def me(s: Sesion = Depends(sesion_actual), db: Session = Depends(get_db)):
    u = s.usuario
    base = {
        "id": u.id, "nombre": u.nombre, "correo": u.correo, "rol": u.rol,
        "debe_cambiar_password": u.debe_cambiar_password,
        "suplantando": s.suplantando, "solo_lectura": s.solo_lectura,
    }
    if not s.empresa_id:
        return {**base, "ambito": "plataforma", "empresa": None, "licencia": None, "modulos": []}
    empresa = db.get(Empresa, s.empresa_id)
    est = resolver(db, empresa)
    visibles = [k for k in modulos_de_usuario(u, est) if k in catalog.ROLES_POR_MODULO]
    orden = list(catalog.MODULOS)
    return {
        **base,
        "ambito": "empresa",
        "empresa": _marca(empresa),
        "licencia": {
            "estado": est.estado, "mensaje": est.mensaje, "fin": est.fin,
            "dias_restantes": est.dias_restantes, "escritura": est.escritura, "limites": est.limites,
        },
        "modulos": sorted(visibles, key=orden.index),
    }


@router.post("/cambiar-password")
def cambiar_password(datos: CambiarPasswordIn, request: Request, s: Sesion = Depends(sesion_actual), db: Session = Depends(get_db)):
    if s.suplantando:
        raise HTTPException(403, "No disponible durante la suplantación")
    if not verify_password(datos.actual, s.usuario.password_hash):
        raise HTTPException(400, "La contraseña actual no es correcta")
    s.usuario.password_hash = hash_password(datos.nueva)
    s.usuario.debe_cambiar_password = False
    registrar(db, accion="cambiar_password", usuario=s.usuario, empresa_id=s.usuario.empresa_id, request=request)
    db.commit()
    return {"ok": True}
